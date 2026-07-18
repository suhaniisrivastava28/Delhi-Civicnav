import os
import json
import re
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='static')

# =====================================================================
# ELASTICSEARCH CLIENT CONFIGURATION
# =====================================================================
# Connects to a live Elasticsearch cluster. Falls back to an in-memory
# simulator if the cluster is unreachable.
# =====================================================================

ES_HOST = os.environ.get("ES_HOST", "http://localhost:9200")
USE_REAL_ES = False  # Will be set to True if connection succeeds

try:
    from elasticsearch import Elasticsearch as RealElasticsearch
    from elasticsearch.helpers import bulk
    _es_client = RealElasticsearch(
        ES_HOST,
        verify_certs=False,
        request_timeout=5,
        max_retries=1,
        retry_on_timeout=False
    )
    # Test connection
    info = _es_client.info()
    print(f"[OK] Connected to Elasticsearch {info['version']['number']} at {ES_HOST}")
    USE_REAL_ES = True
except Exception as e:
    print(f"[WARN] Elasticsearch not available at {ES_HOST}: {e}")
    print("   Falling back to in-memory ElasticsearchSimulator.")
    _es_client = None

# =====================================================================
# UNIFIED ES WRAPPER (Real ES or Simulator — same interface)
# =====================================================================

class ElasticsearchSimulator:
    """In-memory fallback that mimics the elasticsearch-py client API."""
    def __init__(self):
        self._indices = {
            "localities": [],
            "agency_rules": []
        }

    def index(self, index, id, body):
        body["_sim_id"] = id
        if index not in self._indices:
            self._indices[index] = []
        self._indices[index].append(body)

    def _tokenize(self, text):
        if not text:
            return set()
        text = str(text).lower()
        words = re.findall(r'\b[a-z0-9]+\b', text)
        tokens = set()
        for w in words:
            tokens.add(w)
            if len(w) > 3:
                if w.endswith('s'):
                    if w.endswith('es') and len(w) > 4:
                        tokens.add(w[:-2])
                    tokens.add(w[:-1])
        return tokens

    def search(self, index, body):
        if index not in self._indices:
            return {"hits": {"total": {"value": 0}, "max_score": 0.0, "hits": []}}

        docs = self._indices[index]
        query = body.get("query", {})
        results = []

        for doc in docs:
            score = 0.0
            matched = False

            if "bool" in query:
                bool_query = query["bool"]
                
                must_clause = bool_query.get("must", [])
                must_failed = False
                for clause in must_clause:
                    clause_score, clause_match = self._evaluate_clause(doc, clause)
                    if not clause_match:
                        must_failed = True
                        break
                    score += clause_score
                
                if must_failed and must_clause:
                    continue

                filter_clause = bool_query.get("filter", [])
                filter_failed = False
                for clause in filter_clause:
                    _, clause_match = self._evaluate_clause(doc, clause)
                    if not clause_match:
                        filter_failed = True
                        break
                
                if filter_failed and filter_clause:
                    continue

                should_clause = bool_query.get("should", [])
                should_matched = False
                should_score = 0.0
                for clause in should_clause:
                    clause_score, clause_match = self._evaluate_clause(doc, clause)
                    if clause_match:
                        should_matched = True
                        should_score += clause_score
                
                if should_clause:
                    if not should_matched and not must_clause:
                        continue
                    score += should_score
                    matched = True
                else:
                    matched = True if (not must_clause or not must_failed) else False

            else:
                score, matched = self._evaluate_clause(doc, query)

            if matched or score > 0:
                results.append({
                    "_index": index,
                    "_id": doc.get("_sim_id"),
                    "_score": round(score, 4),
                    "_source": {k: v for k, v in doc.items() if k != "_sim_id"}
                })

        results.sort(key=lambda x: x["_score"], reverse=True)

        return {
            "hits": {
                "total": {"value": len(results)},
                "max_score": results[0]["_score"] if results else 0.0,
                "hits": results
            }
        }

    def _evaluate_clause(self, doc, clause):
        if "term" in clause:
            field, value = list(clause["term"].items())[0]
            doc_val = doc.get(field, "")
            if str(doc_val).lower() == str(value).lower():
                return 2.0, True
            return 0.0, False

        elif "match" in clause:
            field, value = list(clause["match"].items())[0]
            doc_val = doc.get(field, "")
            query_tokens = self._tokenize(value)
            doc_tokens = self._tokenize(doc_val)
            intersection = query_tokens.intersection(doc_tokens)
            if intersection:
                score = (len(intersection) / len(query_tokens)) * 5.0
                return score, True
            return 0.0, False

        elif "multi_match" in clause:
            mm = clause["multi_match"]
            query_val = mm.get("query", "")
            fields = mm.get("fields", [])
            query_tokens = self._tokenize(query_val)
            
            max_score = 0.0
            matched = False
            for raw_field in fields:
                boost = 1.0
                field = raw_field
                if "^" in raw_field:
                    field, boost_str = raw_field.split("^")
                    boost = float(boost_str)
                
                doc_val = doc.get(field, "")
                if isinstance(doc_val, list):
                    doc_val_str = " ".join(doc_val)
                else:
                    doc_val_str = str(doc_val)
                
                doc_tokens = self._tokenize(doc_val_str)
                intersection = query_tokens.intersection(doc_tokens)
                if intersection:
                    score = (len(intersection) / len(query_tokens)) * 5.0 * boost
                    if score > max_score:
                        max_score = score
                    matched = True
            
            return max_score, matched

        return 0.0, False


class ElasticsearchWrapper:
    """Unified wrapper — delegates to real ES or simulator with the same interface."""
    
    def __init__(self, real_client, use_real):
        self.real = real_client
        self.use_real = use_real
        self.simulator = ElasticsearchSimulator()
        self._index_data = {}  # Keep a copy of indexed data for inspection & re-seeding
    
    def create_indices(self, localities_data, agency_rules_data):
        """Create indices with proper mappings and seed data."""
        self._index_data["localities"] = localities_data
        self._index_data["agency_rules"] = agency_rules_data
        
        if self.use_real:
            self._create_real_indices(localities_data, agency_rules_data)
        
        # Always seed the simulator as well (for inspection endpoints)
        for idx, loc in enumerate(localities_data):
            self.simulator.index("localities", id=f"loc_{idx}", body=dict(loc))
        for idx, rule in enumerate(agency_rules_data):
            self.simulator.index("agency_rules", id=f"rule_{idx}", body=dict(rule))
    
    def _create_real_indices(self, localities_data, agency_rules_data):
        """Create real Elasticsearch indices with custom mappings."""
        # Index settings with custom analyzer
        settings = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "civic_analyzer": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase", "english_stemmer", "civic_synonyms"]
                        }
                    },
                    "filter": {
                        "english_stemmer": {
                            "type": "stemmer",
                            "language": "english"
                        },
                        "civic_synonyms": {
                            "type": "synonym",
                            "synonyms": [
                                "pothole,crater,pit",
                                "sewer,sewage,drain",
                                "garbage,trash,waste,rubbish",
                                "electricity,power,current,bijli",
                                "water,paani,jal",
                                "road,street,sadak,marg",
                                "encroachment,illegal,unauthorized"
                            ]
                        }
                    }
                }
            }
        }
        
        # Localities index mapping
        localities_mapping = {
            **settings,
            "mappings": {
                "properties": {
                    "name": {"type": "text", "analyzer": "civic_analyzer", "fields": {"keyword": {"type": "keyword"}}},
                    "pin": {"type": "keyword"},
                    "mcd_zone": {"type": "keyword"},
                    "discom": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "sub_division": {"type": "text", "analyzer": "civic_analyzer", "fields": {"keyword": {"type": "keyword"}}},
                    "type": {"type": "keyword"}
                }
            }
        }
        
        # Agency rules index mapping
        agency_rules_mapping = {
            **settings,
            "mappings": {
                "properties": {
                    "category": {"type": "text", "analyzer": "civic_analyzer", "fields": {"keyword": {"type": "keyword"}}},
                    "keywords": {"type": "text", "analyzer": "civic_analyzer"},
                    "default_agency": {"type": "keyword"},
                    "agency_full_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "description": {"type": "text", "analyzer": "civic_analyzer"},
                    "documents_required": {"type": "text"},
                    "helpline": {"type": "object", "enabled": False},
                    "draft_template_en": {"type": "text", "index": False},
                    "draft_template_hi": {"type": "text", "index": False}
                }
            }
        }
        
        try:
            # Delete and recreate indices
            for idx_name in ["localities", "agency_rules"]:
                if self.real.indices.exists(index=idx_name):
                    self.real.indices.delete(index=idx_name)
                    print(f"   Deleted existing index: {idx_name}")
            
            self.real.indices.create(index="localities", body=localities_mapping)
            print("   [OK] Created index: localities")
            
            self.real.indices.create(index="agency_rules", body=agency_rules_mapping)
            print("   [OK] Created index: agency_rules")
            
            # Bulk index localities
            loc_actions = []
            for idx, loc in enumerate(localities_data):
                loc_actions.append({
                    "_index": "localities",
                    "_id": f"loc_{idx}",
                    "_source": loc
                })
            success, errors = bulk(self.real, loc_actions)
            print(f"   [OK] Indexed {success} localities ({errors} errors)")
            
            # Bulk index agency rules
            rule_actions = []
            for idx, rule in enumerate(agency_rules_data):
                rule_actions.append({
                    "_index": "agency_rules",
                    "_id": f"rule_{idx}",
                    "_source": rule
                })
            success, errors = bulk(self.real, rule_actions)
            print(f"   [OK] Indexed {success} agency rules ({errors} errors)")
            
            # Refresh indices to make data searchable
            self.real.indices.refresh(index="localities")
            self.real.indices.refresh(index="agency_rules")
            print("   [OK] Indices refreshed and ready")
            
        except Exception as e:
            print(f"   [ERR] Error creating real ES indices: {e}")
            print("   Falling back to simulator.")
            self.use_real = False
    
    def search(self, index, body):
        """Search using real ES or simulator."""
        if self.use_real:
            try:
                res = self.real.search(index=index, body=body)
                return {
                    "hits": {
                        "total": {"value": res["hits"]["total"]["value"]},
                        "max_score": res["hits"]["max_score"] or 0.0,
                        "hits": [
                            {
                                "_index": hit["_index"],
                                "_id": hit["_id"],
                                "_score": hit["_score"] or 0.0,
                                "_source": hit["_source"]
                            }
                            for hit in res["hits"]["hits"]
                        ]
                    }
                }
            except Exception as e:
                print(f"   ES search error (falling back to simulator): {e}")
                return self.simulator.search(index, body)
        else:
            return self.simulator.search(index, body)
    
    def get_index_data(self, index_name):
        """Get raw indexed data for inspection."""
        return self._index_data.get(index_name, [])
    
    def get_index_names(self):
        """Get all index names."""
        return list(self._index_data.keys())
    
    def get_cluster_info(self):
        """Get cluster connection info."""
        if self.use_real:
            try:
                info = self.real.info()
                return {
                    "mode": "Real Elasticsearch",
                    "version": info["version"]["number"],
                    "cluster_name": info["cluster_name"],
                    "host": ES_HOST
                }
            except:
                pass
        return {
            "mode": "In-Memory Simulator (fallback)",
            "version": "simulator",
            "cluster_name": "local-simulator",
            "host": "in-memory"
        }

# Instantiate the wrapper
es = ElasticsearchWrapper(_es_client, USE_REAL_ES)


# =====================================================================
# DATA SEEDING (LOCALITIES & RULES)
# =====================================================================

# 1. Localities Database
localities_data = [
    # South Delhi
    {"name": "Saket", "pin": "110017", "mcd_zone": "South", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Saket", "type": "MCD"},
    {"name": "Vasant Kunj", "pin": "110070", "mcd_zone": "South", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Vasant Kunj", "type": "MCD/DDA"},
    {"name": "Lajpat Nagar", "pin": "110024", "mcd_zone": "Central", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Lajpat Nagar", "type": "MCD"},
    {"name": "Kalkaji", "pin": "110019", "mcd_zone": "Central", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Kalkaji", "type": "MCD"},
    {"name": "Greater Kailash", "pin": "110048", "mcd_zone": "South", "discom": "BSES Rajdhani (BRPL)", "sub_division": "GK-1", "type": "MCD"},
    {"name": "Malviya Nagar", "pin": "110017", "mcd_zone": "South", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Malviya Nagar", "type": "MCD"},
    {"name": "Hauz Khas", "pin": "110016", "mcd_zone": "South", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Hauz Khas", "type": "MCD"},
    {"name": "Safdarjung Enclave", "pin": "110029", "mcd_zone": "South", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Safdarjung", "type": "MCD"},
    {"name": "Okhla", "pin": "110025", "mcd_zone": "Central", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Okhla", "type": "MCD"},
    
    # West Delhi
    {"name": "Dwarka", "pin": "110075", "mcd_zone": "Najafgarh", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Dwarka", "type": "DDA/MCD"},
    {"name": "Janakpuri", "pin": "110058", "mcd_zone": "West", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Janakpuri", "type": "MCD"},
    {"name": "Rajouri Garden", "pin": "110027", "mcd_zone": "West", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Rajouri Garden", "type": "MCD"},
    {"name": "Punjabi Bagh", "pin": "110026", "mcd_zone": "West", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Punjabi Bagh", "type": "MCD"},
    {"name": "Vikaspuri", "pin": "110018", "mcd_zone": "West", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Vikaspuri", "type": "MCD"},
    {"name": "Paschim Vihar", "pin": "110063", "mcd_zone": "West", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Paschim Vihar", "type": "MCD"},
    {"name": "Najafgarh", "pin": "110043", "mcd_zone": "Najafgarh", "discom": "BSES Rajdhani (BRPL)", "sub_division": "Najafgarh", "type": "MCD"},
    
    # North & Northwest Delhi
    {"name": "Rohini Sector 8", "pin": "110085", "mcd_zone": "Rohini", "discom": "Tata Power (TPDDL)", "sub_division": "Rohini", "type": "DDA/MCD"},
    {"name": "Rohini Sector 3", "pin": "110085", "mcd_zone": "Rohini", "discom": "Tata Power (TPDDL)", "sub_division": "Rohini", "type": "DDA/MCD"},
    {"name": "Pitampura", "pin": "110034", "mcd_zone": "Keshavpuram", "discom": "Tata Power (TPDDL)", "sub_division": "Pitampura", "type": "MCD"},
    {"name": "Shalimar Bagh", "pin": "110088", "mcd_zone": "Keshavpuram", "discom": "Tata Power (TPDDL)", "sub_division": "Shalimar Bagh", "type": "MCD"},
    {"name": "Model Town", "pin": "110009", "mcd_zone": "Civil Lines", "discom": "Tata Power (TPDDL)", "sub_division": "Model Town", "type": "MCD"},
    {"name": "Narela", "pin": "110040", "mcd_zone": "Narela", "discom": "Tata Power (TPDDL)", "sub_division": "Narela", "type": "MCD"},
    {"name": "Civil Lines", "pin": "110054", "mcd_zone": "Civil Lines", "discom": "Tata Power (TPDDL)", "sub_division": "Civil Lines", "type": "MCD"},
    {"name": "Ashok Vihar", "pin": "110052", "mcd_zone": "Keshavpuram", "discom": "Tata Power (TPDDL)", "sub_division": "Ashok Vihar", "type": "MCD"},
    
    # East Delhi & Central
    {"name": "Mayur Vihar Phase 1", "pin": "110091", "mcd_zone": "Shahdara South", "discom": "BSES Yamuna (BYPL)", "sub_division": "Mayur Vihar", "type": "MCD"},
    {"name": "Mayur Vihar Phase 2", "pin": "110091", "mcd_zone": "Shahdara South", "discom": "BSES Yamuna (BYPL)", "sub_division": "Mayur Vihar", "type": "MCD"},
    {"name": "Laxmi Nagar", "pin": "110092", "mcd_zone": "Shahdara South", "discom": "BSES Yamuna (BYPL)", "sub_division": "Laxmi Nagar", "type": "MCD"},
    {"name": "Preet Vihar", "pin": "110092", "mcd_zone": "Shahdara South", "discom": "BSES Yamuna (BYPL)", "sub_division": "Preet Vihar", "type": "MCD"},
    {"name": "Shahdara", "pin": "110032", "mcd_zone": "Shahdara North", "discom": "BSES Yamuna (BYPL)", "sub_division": "Shahdara", "type": "MCD"},
    {"name": "Dilshad Garden", "pin": "110095", "mcd_zone": "Shahdara North", "discom": "BSES Yamuna (BYPL)", "sub_division": "Dilshad Garden", "type": "MCD"},
    {"name": "Karol Bagh", "pin": "110005", "mcd_zone": "Karol Bagh", "discom": "BSES Yamuna (BYPL)", "sub_division": "Karol Bagh", "type": "MCD"},
    {"name": "Connaught Place", "pin": "110001", "mcd_zone": "NDMC", "discom": "NDMC", "sub_division": "Connaught Place", "type": "NDMC"},
    {"name": "Chanakyapuri", "pin": "110021", "mcd_zone": "NDMC", "discom": "NDMC", "sub_division": "Chanakyapuri", "type": "NDMC"},
    {"name": "Delhi Cantonment", "pin": "110010", "mcd_zone": "Cantonment Board", "discom": "Cantonment Board / MES", "sub_division": "Delhi Cantt", "type": "Cantonment"}
]

# Note: data will be indexed below after agency_rules_data is defined

# 2. Jurisdiction Rules Database
agency_rules_data = [
    # ------------------ WATER SUPPLY & QUALITY ------------------
    {
        "category": "Water Supply & Quality",
        "keywords": ["water supply", "dirty water", "no water", "contaminated water", "water pipe leak", "water billing", "low pressure", "jal board", "water tanker"],
        "default_agency": "DJB",
        "description": "Drinking water supply, contamination, pipelines, billing, and services handled by Delhi Jal Board.",
        "documents_required": [
            "Latest Delhi Jal Board (DJB) Water Bill (copy showing K.No/Consumer ID)",
            "Photographs of contaminated water or pipeline leakage (clearly showing locality context)",
            "Identity Proof (Aadhaar Card, Voter ID, or Passport)",
            "Address Proof matching the complaint address (if different from ID)"
        ],
        "draft_template_en": """To,
The Assistant Engineer / Zonal Officer (Water),
Delhi Jal Board,
Government of NCT of Delhi,
{zone_info} Division, Delhi.

Subject: Formal Complaint regarding {short_summary} at {address}

Dear Sir/Madam,

I am writing to bring to your immediate attention a critical issue concerning {short_summary} in our locality.

Details of the issue:
- Location: {address}, {locality_name} (Landmark: {landmark})
- PIN Code: {pin}
- Affected Resident: {name} (Contact: {contact})
- Date of onset: Recent days and ongoing.
- Detailed Description: {complaint_text}

This issue is causing severe inconvenience to the residents of the locality. {additional_comments}

Therefore, I request you to depute an inspection team to resolve this issue on top priority. 

Thanking you.

Yours faithfully,
{name}
Address: {address}
Contact: {contact}""",
        "draft_template_hi": """सेवा में,
सहायक अभियंता / क्षेत्रीय अधिकारी (जल),
दिल्ली जल बोर्ड,
राष्ट्रीय राजधानी क्षेत्र दिल्ली सरकार,
{zone_info} मंडल, दिल्ली।

विषय: {short_summary} के संबंध में शिकायत (स्थान: {address})

महोदय/महोदया,

मैं इस पत्र के माध्यम से हमारे क्षेत्र में {short_summary} से संबंधित एक गंभीर समस्या की ओर आपका ध्यान आकर्षित करना चाहता हूँ।

विवरण इस प्रकार है:
- शिकायतकर्ता का नाम: {name}
- संपर्क सूत्र: {contact}
- शिकायत का पता: {address}, {locality_name} (नजदीकी स्थान: {landmark})
- पिन कोड: {pin}
- शिकायत का विवरण: {complaint_text}

इस समस्या के कारण क्षेत्र के निवासियों को भारी कठिनाई का सामना करना पड़ रहा है। {additional_comments}

अतः आपसे विनम्र निवेदन है कि इस समस्या के तत्काल समाधान हेतु संबंधित अधिकारियों को निर्देशित करने की कृपा करें।

धन्यवाद।

भवदीय,
{name}
पता: {address}
दूरभाष: {contact}"""
    },

    # ------------------ SEWAGE & DRAINAGE ------------------
    {
        "category": "Sewage Blockage & Overflow",
        "keywords": ["sewage", "sewer line", "overflowing sewer", "manhole open", "clogged sewer", "dirty drainage"],
        "default_agency": "DJB",
        "description": "Major sewage pipelines, blockages, open manholes, and mainline overflows are managed by Delhi Jal Board (DJB). (Colony storm-water drains are managed by MCD, and arterial road drains by PWD).",
        "documents_required": [
            "Clear photograph showing the overflowing sewage/manhole in relation to the street",
            "Address proof of the complainant",
            "Signatures of affected neighbors (optional, but recommended for community sewerage blocks)"
        ],
        "draft_template_en": """To,
The Assistant Engineer (Sewerage),
Delhi Jal Board,
Government of NCT of Delhi,
{zone_info} Zone, Delhi.

Subject: Urgent Complaint: Sewerage Overflow / Blockage at {address}

Dear Sir/Madam,

I wish to report a major sewerage blockage and overflow in our lane/colony which is causing highly unsanitary conditions.

Details of the problem:
- Site Address: {address}, {locality_name} (Landmark: {landmark})
- PIN Code: {pin}
- Reporter Name: {name} (Contact: {contact})
- Description: {complaint_text}

The overflow is creating a severe health hazard, breeding mosquitoes, and making it impossible for pedestrians to walk. {additional_comments}

We request you to send a jetting machine / sewer maintenance crew to clear the blockage immediately.

Thanking you.

Yours sincerely,
{name}
Address: {address}
Contact: {contact}""",
        "draft_template_hi": """सेवा में,
सहायक अभियंता (सीवरेज),
दिल्ली जल बोर्ड,
दिल्ली सरकार,
{zone_info} क्षेत्र, दिल्ली।

विषय: सीवर ओवरफ्लो और गंदगी की गंभीर समस्या के संबंध में (स्थान: {address})

महोदय/महोदया,

मैं इस पत्र के माध्यम से हमारे क्षेत्र में सीवर के मुख्य पाइपलाइन ब्लॉक होने और उसके पानी के सड़क पर बहने की शिकायत दर्ज कराना चाहता हूँ।

विवरण:
- पता: {address}, {locality_name} (नजदीकी स्थान: {landmark})
- पिन: {pin}
- शिकायतकर्ता: {name} (दूरभाष: {contact})
- समस्या का विवरण: {complaint_text}

इस सीवर के गंदे पानी के कारण महामारी फैलने का खतरा बना हुआ है और लोगों का घर से निकलना दूभर हो गया है। {additional_comments}

अतः आपसे अनुरोध है कि जल्द से जल्द सीवर सफाई मशीन भेजकर इसे ठीक कराएं।

धन्यवाद।

भवदीय,
{name}
पता: {address}
दूरभाष: {contact}"""
    },

    # ------------------ ELECTRICITY / POWER / DISCOMs ------------------
    {
        "category": "Electricity & DISCOM Services",
        "keywords": ["power cut", "electricity bill", "electric meter", "faulty meter", "hanging wires", "sparking wire", "transformer blast", "electricity pole", "power theft", "naked cables"],
        "default_agency": "DISCOM",
        "description": "Electricity supply, power cuts, meter problems, hanging cables, and electric shock hazards fall under the jurisdiction of the region's DISCOM (BSES BRPL/BYPL or Tata Power TPDDL).",
        "documents_required": [
            "Latest Electricity Bill copy (showing CA Number / Consumer Account Number)",
            "Photograph of the faulty meter, spark point, or dangerous hanging wires",
            "Identity proof of the registered consumer"
        ],
        "draft_template_en": """To,
The Business Manager / Division Head,
{agency_full_name},
Government of NCT of Delhi,
{sub_division} Division, Delhi.

Subject: Complaint regarding {short_summary} at {address}

Dear Sir/Madam,

I am writing to log an official complaint regarding {short_summary} at our premises / locality.

Consumer Details:
- Name of Consumer: {name}
- Contact Number: {contact}
- Address: {address}, {locality_name} (Landmark: {landmark})
- PIN Code: {pin}
- CA / Account No (if billing/meter issue): [Please insert your Account Number here]
- Description of Issue: {complaint_text}

This issue requires urgent technical intervention to prevent safety hazards or financial disputes. {additional_comments}

Please register this ticket and send a field engineer to inspect and resolve this immediately.

Thanking you.

Yours faithfully,
{name}
Address: {address}
Contact: {contact}""",
        "draft_template_hi": """सेवा में,
बिजनेस मैनेजर / मंडल प्रमुख,
{agency_full_name},
दिल्ली सरकार,
{sub_division} मंडल, दिल्ली।

विषय: {short_summary} के संबंध में शिकायत दर्ज कराने बाबत (स्थान: {address})

महोदय/महोदया,

मैं आपके क्षेत्र के उपभोक्ता के रूप में {short_summary} के संबंध में शिकायत दर्ज कराना चाहता हूँ।

उपभोक्ता विवरण:
- नाम: {name}
- मोबाइल: {contact}
- उपभोक्ता का पता: {address}, {locality_name} (नजदीकी स्थान: {landmark})
- पिन कोड: {pin}
- सी.ए. (CA) नंबर: [कृपया यहाँ अपना बिजली बिल अकाउंट नंबर लिखें]
- शिकायत का विवरण: {complaint_text}

यह समस्या न केवल असुविधाजनक है बल्कि [यदि तार लटके हैं तो: सुरक्षा के लिए भी खतरनाक है]। {additional_comments}

कृपया इस शिकायत पर त्वरित कार्रवाई करते हुए तकनीकी टीम को भेजने की कृपा करें।

धन्यवाद।

भवदीय,
{name}
पता: {address}
दूरभाष: {contact}"""
    },

    # ------------------ SANITATION & GARBAGE (MCD) ------------------
    {
        "category": "Sanitation & Garbage Management",
        "keywords": ["garbage", "trash piling", "dump yard", "dhalao overflow", "street sweepers", "dead animal removal", "mcd sanitation", "litter", "unclean street", "dustbin"],
        "default_agency": "MCD",
        "description": "Colony cleanliness, street sweeping, municipal garbage bins (dhalaos), waste lifting, and carcass disposal are managed by the Municipal Corporation of Delhi (MCD).",
        "documents_required": [
            "Geotagged photographs of the garbage pile / uncleansed street (highly effective for MCD 311)",
            "Specific landmark description to assist sanitation workers in location detection"
        ],
        "draft_template_en": """To,
The Sanitary Inspector / Assistant Commissioner,
Municipal Corporation of Delhi (MCD),
{zone_info} Zone, Delhi.

Subject: Complaint regarding Garbage Piling & Lack of Sanitation at {address}

Dear Sir/Madam,

I am writing to highlight the pathetic sanitary condition in our neighborhood due to regular piling up of garbage and the absence of street sweeping.

Details:
- Site Location: {address}, {locality_name} (Landmark: {landmark})
- PIN Code: {pin}
- Reported By: {name} (Contact: {contact})
- Description: {complaint_text}

The garbage has not been cleared for several days, leading to foul odor and attracting stray animals, creating severe health hazards. {additional_comments}

We request you to direct the sanitation staff (safai karamcharis) to clear this garbage immediately and ensure regular cleaning of our lane.

Thanking you.

Yours sincerely,
{name}
Address: {address}
Contact: {contact}""",
        "draft_template_hi": """सेवा में,
सफाई निरीक्षक / सहायक आयुक्त,
दिल्ली नगर निगम (MCD),
{zone_info} क्षेत्र, दिल्ली।

विषय: {locality_name} में कचरा जमा होने और साफ-सफाई न होने के संबंध में शिकायत।

महोदय/महोदया,

मैं इस पत्र के माध्यम से आपके क्षेत्र के अंतर्गत आने वाले इलाके में जमा कचरे और नियमित सफाई न होने के कारण उत्पन्न गंदगी की ओर आपका ध्यान आकर्षित करना चाहता हूँ।

विवरण:
- स्थल का पता: {address}, {locality_name} (नजदीकी स्थान: {landmark})
- पिन कोड: {pin}
- शिकायतकर्ता: {name} (दूरभाष: {contact})
- समस्या: {complaint_text}

यहाँ कई दिनों से कूड़ा नहीं उठाया गया है, जिससे असहनीय बदबू आ रही है और आवारा पशुओं का जमावड़ा रहता है। {additional_comments}

आपसे अनुरोध है कि सफाई कर्मचारियों को कूड़ा उठाने और नियमित रूप से झाड़ू लगाने के निर्देश दें।

धन्यवाद।

भवदीय,
{name}
पता: {address}
दूरभाष: {contact}"""
    },

    # ------------------ ROADS & POTHOLES (MCD / PWD / DDA) ------------------
    {
        "category": "Road Maintenance & Potholes",
        "keywords": ["pothole", "broken road", "caved in road", "road construction", "pavement repair", "road maintenance", "broken footpath", "road cut"],
        "default_agency": "MCD", # Fallback, dynamic check in agent
        "description": "Road maintenance. Colony roads (<60ft wide) are MCD. Arterial/Main roads (>=60ft wide), flyovers, and underpasses are PWD. Un-handovered housing zones are DDA.",
        "documents_required": [
            "Photographs of the pothole or broken road stretch (including wider angle showing road width to prove jurisdiction)",
            "Exact GPS Coordinates or link to Google Map location (optional but highly recommended)"
        ],
        "draft_template_en": """To,
The Executive Engineer (Maintenance),
{agency_full_name},
{zone_info} Division, Delhi.

Subject: Complaint regarding Broken Road / Dangerous Potholes at {address}

Dear Sir/Madam,

I am writing to register an urgent complaint regarding the poor state of the road stretch in our locality which has developed deep potholes.

Location Details:
- Road Location: {address}, {locality_name} (Landmark: {landmark})
- PIN Code: {pin}
- Classification: {road_classification_detail}
- Reported By: {name} (Contact: {contact})
- Description: {complaint_text}

These potholes pose a major accident risk, especially for two-wheelers and during rainy conditions. {additional_comments}

We request you to carry out patching/repair works on this road stretch on priority before any major mishap occurs.

Thanking you.

Yours faithfully,
{name}
Address: {address}
Contact: {contact}""",
        "draft_template_hi": """सेवा में,
अधिशासी अभियंता (रखरखाव),
{agency_full_name},
{zone_info} मंडल, दिल्ली।

विषय: {address} पर टूटी सड़क और खतरनाक गड्ढों की मरम्मत के संबंध में।

महोदय/महोदया,

मैं इस पत्र के द्वारा हमारे क्षेत्र में सड़क की जर्जर हालत और उसमें बने गहरे गड्ढों की ओर आपका ध्यान आकर्षित करना चाहता हूँ, जो दुर्घटना का सबब बने हुए हैं।

विवरण:
- स्थान: {address}, {locality_name} (नजदीकी स्थान: {landmark})
- पिन: {pin}
- मार्ग का प्रकार: {road_classification_detail}
- शिकायतकर्ता: {name} (दूरभाष: {contact})
- विवरण: {complaint_text}

ये गड्ढे विशेष रूप से दुपहिया वाहनों के लिए बहुत घातक हैं। {additional_comments}

आपसे अनुरोध है कि दुर्घटना होने से पहले सड़क की मरम्मत/पैचिंग का कार्य कराने की कृपा करें।

धन्यवाद।

भवदीय,
{name}
पता: {address}
दूरभाष: {contact}"""
    },

    # ------------------ TRAFFIC & PARKING ------------------
    {
        "category": "Traffic & Parking Violations",
        "keywords": ["illegal parking", "traffic jam", "broken traffic light", "traffic signal", "no parking zone", "abandoned vehicle", "road blockage", "encroached footpath traffic", "wrong way driving"],
        "default_agency": "Traffic Police",
        "description": "Traffic flow management, broken traffic lights, wrong-way driving, and illegal parking on public/main roads are handled by the Delhi Traffic Police.",
        "documents_required": [
            "Clear photograph showing the illegally parked vehicle (with visible license plate) or broken signal",
            "Exact location and time of violation"
        ],
        "draft_template_en": """To,
The Deputy Commissioner of Police (Traffic),
Delhi Traffic Police HQ,
Todapur, New Delhi.
Traffic District: {zone_info} Traffic Circle.

Subject: Complaint regarding Traffic Violation / Signal Breakdown at {address}

Dear Sir/Madam,

I am writing to report a persistent traffic issue in our area that is causing severe congestion and public hazard.

Details:
- Location: {address}, {locality_name} (Landmark: {landmark})
- PIN Code: {pin}
- Issue Reported: {complaint_text}
- Contact Person: {name} (Contact: {contact})

This issue creates major gridlock daily and disrupts regular commuting. {additional_comments}

We request you to deploy traffic personnel / route a crane to clear the illegal parking or repair the signal.

Thanking you.

Yours faithfully,
{name}
Address: {address}
Contact: {contact}""",
        "draft_template_hi": """सेवा में,
पुलिस उपायुक्त (यातायात),
दिल्ली ट्रैफिक पुलिस मुख्यालय,
टोडापुर, नई दिल्ली।
यातायात सर्कल: {zone_info} सर्किल।

विषय: {address} पर यातायात उल्लंघन / खराब सिग्नल के संबंध में।

महोदय/महोदया,

मैं इस पत्र के माध्यम से हमारे क्षेत्र में रोज होने वाली यातायात जाम और अवैध पार्किंग की समस्या की शिकायत करना चाहता हूँ।

विवरण:
- पता: {address}, {locality_name} (नजदीकी स्थान: {landmark})
- पिन: {pin}
- समस्या: {complaint_text}
- शिकायतकर्ता: {name} (दूरभाष: {contact})

अवैध रूप से पार्क किए गए वाहनों के कारण मुख्य मार्ग पूरी तरह से बाधित हो जाता है। {additional_comments}

कृपया इस मार्ग पर क्रेन भेजने या ट्रैफ़िक पुलिसकर्मी की नियुक्ति कर मार्ग को सुगम बनाने की कृपा करें।

धन्यवाद।

भवदीय,
{name}
पता: {address}
दूरभाष: {contact}"""
    },

    # ------------------ ENCROACHMENTS & PROPERTY (MCD / DDA) ------------------
    {
        "category": "Illegal Encroachments & Construction",
        "keywords": ["illegal construction", "encroachment", "unauthorized construction", "building bye-laws violation", "encroached pavement", "shop extension"],
        "default_agency": "MCD",
        "description": "Unauthorized building construction, violations of building bylaws, and shop extensions blocking public lanes fall under MCD (or DDA for DDA-owned vacant lands/markets).",
        "documents_required": [
            "Recent photographs of the unauthorized construction/encroachment",
            "Property number and boundary details",
            "Copy of any public land demarcation registry (if available)"
        ],
        "draft_template_en": """To,
The Deputy Commissioner / Building Department,
Municipal Corporation of Delhi (MCD),
{zone_info} Zone, Delhi.

Subject: Complaint against Unauthorized Encroachment / Illegal Construction at {address}

Dear Sir/Madam,

I wish to register a formal complaint regarding illegal building construction / encroachment on public land in our locality.

Details:
- Site Address: {address}, {locality_name} (Landmark: {landmark})
- PIN Code: {pin}
- Details of Violator / Property (if known): [Please fill in Property details here]
- Nature of encroachment: {complaint_text}
- Reported By: {name} (Contact: {contact})

This unauthorized construction encroaches onto public paths and is a clear violation of Delhi Building Bye-Laws. {additional_comments}

I request you to verify the approvals for this building and take demobilization or demolition action if found illegal.

Thanking you.

Yours faithfully,
{name}
Address: {address}
Contact: {contact}""",
        "draft_template_hi": """सेवा में,
उपायुक्त / भवन विभाग,
दिल्ली नगर निगम (MCD),
{zone_info} क्षेत्र, दिल्ली।

विषय: {address} पर सार्वजनिक भूमि पर अवैध अतिक्रमण / अनधिकृत निर्माण की शिकायत।

महोदय/महोदया,

मैं इस पत्र के द्वारा हमारे इलाके में सार्वजनिक रास्ते पर किए जा रहे अवैध कब्जे और अनाधिकृत निर्माण की शिकायत दर्ज कराना चाहता हूँ।

विवरण:
- निर्माण का स्थान: {address}, {locality_name} (नजदीकी स्थान: {landmark})
- पिन: {pin}
- शिकायतकर्ता: {name} (दूरभाष: {contact})
- अवैध अतिक्रमण का स्वरूप: {complaint_text}

यह निर्माण न केवल सरकारी नियमों के विरुद्ध है बल्कि राहगीरों के मार्ग को भी अवरुद्ध करता है। {additional_comments}

आपसे अनुरोध है कि भवन विभाग के जूनियर इंजीनियर (JE) को भेजकर इसकी जांच कराएं और आवश्यक कानूनी कार्रवाई करें।

धन्यवाद।

भवदीय,
{name}
पता: {address}
दूरभाष: {contact}"""
    }
]

# Seed both indices (real ES + simulator) via the unified wrapper
es.create_indices(localities_data, agency_rules_data)
print(f"   ES Mode: {'Real Elasticsearch' if es.use_real else 'In-Memory Simulator'}")

# =====================================================================
# AGENT LOGIC (JURISDICTION ROUTING AGENT OVER ES)
# =====================================================================

class JurisdictionAgent:
    def __init__(self, es_wrapper):
        self.es = es_wrapper

    def resolve_locality(self, pin, address, landmark):
        pin_clean = str(pin).strip()
        
        # 1. Attempt to query live public postal API (India Post Directory via api.postalpincode.in)
        if len(pin_clean) == 6 and pin_clean.isdigit():
            try:
                import urllib.request
                import urllib.parse
                url = f"https://api.postalpincode.in/pincode/{pin_clean}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                # 2 second timeout to keep response snappy
                with urllib.request.urlopen(req, timeout=2.0) as response:
                    res_data = json.loads(response.read().decode('utf-8'))
                    
                if res_data and res_data[0].get("Status") == "Success":
                    post_offices = res_data[0].get("PostOffice", [])
                    if post_offices:
                        # Match post office name against address/landmark context if possible
                        matched_po = post_offices[0]
                        for po in post_offices:
                            po_name = po.get("Name", "").lower()
                            if po_name in address.lower() or po_name in landmark.lower():
                                matched_po = po
                                break
                        
                        district = matched_po.get("District", "Central Delhi")
                        division = matched_po.get("Division", "Central")
                        po_name = matched_po.get("Name", "Delhi Locality")
                        
                        # Dynamic Mapping: Translate District name to Delhi MCD zones and DISCOM electricity utilities
                        mcd_zone = "Central"
                        discom = "BSES Rajdhani (BRPL)"
                        
                        dist_lower = district.lower()
                        if "south west" in dist_lower or "west" in dist_lower:
                            discom = "BSES Rajdhani (BRPL)"
                            mcd_zone = "Najafgarh" if "south west" in dist_lower else "West"
                        elif "south" in dist_lower:
                            discom = "BSES Rajdhani (BRPL)"
                            mcd_zone = "South"
                        elif "north west" in dist_lower or "north" in dist_lower:
                            discom = "Tata Power (TPDDL)"
                            mcd_zone = "Rohini" if "north west" in dist_lower else "Civil Lines"
                        elif "east" in dist_lower or "shahdara" in dist_lower or "north east" in dist_lower:
                            discom = "BSES Yamuna (BYPL)"
                            mcd_zone = "Shahdara South" if "east" in dist_lower else "Shahdara North"
                        elif "new delhi" in dist_lower or "ndmc" in dist_lower:
                            discom = "NDMC"
                            mcd_zone = "NDMC"
                        
                        resolved = {
                            "name": f"{po_name} ({district})",
                            "pin": pin_clean,
                            "mcd_zone": mcd_zone,
                            "discom": discom,
                            "sub_division": division,
                            "type": "Public API Match"
                        }
                        return resolved, 5.0
            except Exception as e:
                print("Live public postal API lookup failed or timed out:", e)

        # 2. Fallback: Search local Elasticsearch index
        query = {
            "query": {
                "bool": {
                    "should": [
                        {"term": {"pin": pin_clean}},
                        {"multi_match": {"query": f"{address} {landmark}", "fields": ["name", "sub_division"]}}
                    ]
                }
            }
        }
        res = self.es.search("localities", query)
        hits = res["hits"]["hits"]
        
        if hits:
            # Return top scoring match
            top_hit = hits[0]["_source"]
            score = hits[0]["_score"]
            return top_hit, score
        
        # Generic fallback
        fallback = {
            "name": "Delhi (Unknown Locality)",
            "pin": pin_clean or "110001",
            "mcd_zone": "Central",
            "discom": "BSES Rajdhani (BRPL)",
            "sub_division": "Central Delhi",
            "type": "MCD"
        }
        return fallback, 0.0

    def route_complaint(self, form_data):
        complaint_text = form_data.get("complaint", "")
        additional_comments = form_data.get("additional_comments", "")
        pin = form_data.get("pin", "")
        address = form_data.get("address", "")
        landmark = form_data.get("landmark", "")
        name = form_data.get("name", "")
        contact = form_data.get("contact", "")
        
        # Combine texts for query
        search_text = f"{complaint_text} {additional_comments}"

        # 1. Resolve locality details using ES localities index
        locality, loc_score = self.resolve_locality(pin, address, landmark)

        # 2. Match the complaint category using ES agency_rules index
        rules_query = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": search_text,
                                "fields": ["keywords^2", "category", "description"]
                            }
                        }
                    ]
                }
            }
        }
        
        res = self.es.search("agency_rules", rules_query)
        hits = res["hits"]["hits"]

        if not hits:
            # Ultimate fallback if search fails
            category_match = {
                "category": "General Grievance",
                "default_agency": "MCD",
                "description": "General civic grievance routed to Municipal Corporation of Delhi as standard fallback.",
                "documents_required": ["Identity Proof", "Photograph of the issue"],
                "draft_template_en": "To, The Commissioner...\nComplaint: {complaint_text}",
                "draft_template_hi": "सेवा में, आयुक्त महोदय...\nशिकायत: {complaint_text}"
            }
            match_score = 0.0
        else:
            category_match = hits[0]["_source"]
            match_score = hits[0]["_score"]

        # 3. Dynamic Jurisdiction Logic (solving the complex overlaps)
        final_agency = category_match["default_agency"]
        routing_reason = []
        road_classification_detail = "Local street (< 60 feet width)"

        # Override rules based on specific keywords (solving overlapping jurisdictions)
        
        # --- ROAD OR STREETLIGHT OVERLAPS (MCD vs PWD vs DDA vs NDMC/Cantt) ---
        is_road_or_light = (category_match["category"] == "Road Maintenance & Potholes" or 
                            any(k in search_text.lower() for k in ["streetlight", "street light", "street-light", "high mast"])) and \
                           category_match["category"] not in ["Water Supply & Quality", "Sewage Blockage & Overflow", "Electricity & DISCOM Services", "Traffic & Parking Violations"]
        
        if is_road_or_light:
            # Check if arterial / wide road
            is_arterial = form_data.get("is_main_road", False) or any(k in search_text.lower() for k in ["main road", "flyover", "arterial", "highway", "bus route", "60 feet", "60ft", "wide road", "ring road", "pwd road"])
            if is_arterial:
                final_agency = "PWD"
                road_classification_detail = "Arterial Road / Flyover / Main Highway (>= 60 feet width)"
                routing_reason.append("Complaint contains references to an arterial road, flyover, or highway. Maintenance falls under PWD (Delhi Govt) rather than MCD.")
            else:
                final_agency = "MCD"
                road_classification_detail = "Internal Colony Lane / Local Road (< 60 feet width)"
                routing_reason.append("Routed to MCD because the road/light description matches an internal colony road (< 60 feet wide).")

        # --- SEWAGE OVERLAPS (DJB vs MCD vs PWD) ---
        is_sewer = any(k in search_text.lower() for k in ["sewer", "sewage", "manhole", "gutter"])
        if is_sewer:
            # Check if it's a small street storm water drain vs deep sewer pipeline
            is_storm_drain = any(k in search_text.lower() for k in ["drain", "storm drain", "nalah", "colony drain", "monsoon drain"])
            if is_storm_drain:
                # Is it a main road drain (PWD) or colony drain (MCD)?
                is_pwd_road = any(k in search_text.lower() for k in ["main road", "flyover", "highway", "bus route"])
                if is_pwd_road:
                    final_agency = "PWD"
                    routing_reason.append("Routed to PWD because large storm water drains alongside arterial roads are built and maintained by PWD.")
                else:
                    final_agency = "MCD"
                    routing_reason.append("Routed to MCD because desilting of local colony storm-water drains falls under MCD sanitation.")
            else:
                final_agency = "DJB"
                routing_reason.append("Routed to DJB (Delhi Jal Board) because sewage pipelines, manhole blocks, and underground sewer systems are their exclusive responsibility.")

        # --- DDA PLOTS / FLATS ---
        is_dda_related = any(k in search_text.lower() for k in ["dda flats", "dda park", "dda land", "dda market", "vacant plot", "unauthorized colony"])
        if is_dda_related:
            final_agency = "DDA"
            routing_reason.append("Complaint explicitly references a DDA flats complex, DDA park, or vacant DDA land which has not yet been handed over to MCD.")

        # --- DISCOM Resolution based on Location ---
        if final_agency == "DISCOM":
            final_agency = locality["discom"]
            routing_reason.append(f"Electricity/Power complaint dynamically routed to '{final_agency}' based on the locality lookup for '{locality['name']}'.")

        # --- NDMC / Cantonment Board override ---
        if locality["mcd_zone"] == "NDMC":
            final_agency = "NDMC"
            routing_reason.append("Location falls within the New Delhi Municipal Council (NDMC) area. All municipal, water, and electricity tasks are handled directly by NDMC.")
        elif locality["mcd_zone"] == "Cantonment Board":
            final_agency = "Delhi Cantonment Board"
            routing_reason.append("Location is within the Delhi Cantonment defense area. All civic issues are routed to the Cantonment Board.")

        # Build clean display names and full details
        agency_display_names = {
            "MCD": f"Municipal Corporation of Delhi (MCD) - {locality['mcd_zone']} Zone",
            "DJB": "Delhi Jal Board (DJB)",
            "PWD": f"Public Works Department (PWD) - {locality['sub_division']} Division",
            "DDA": "Delhi Development Authority (DDA)",
            "BSES Rajdhani (BRPL)": "BSES Rajdhani Power Limited (BRPL)",
            "BSES Yamuna (BYPL)": "BSES Yamuna Power Limited (BYPL)",
            "Tata Power (TPDDL)": "Tata Power Delhi Distribution Limited (TPDDL)",
            "NDMC": "New Delhi Municipal Council (NDMC)",
            "Delhi Cantonment Board": "Delhi Cantonment Board",
            "Traffic Police": f"Delhi Traffic Police (Traffic Zone: {locality['mcd_zone']})"
        }
        
        resolved_agency_name = agency_display_names.get(final_agency, final_agency)

        # Standard explanation prefix
        reason_summary = f"Matching Category: {category_match['category']} (Elasticsearch Match Score: {match_score:.2f}). "
        if routing_reason:
            reason_summary += " ".join(routing_reason)
        else:
            reason_summary += f"This issue is standardly routed to {resolved_agency_name}."

        # 4. Generate drafted complaints (English & Hindi)
        zone_info = locality['mcd_zone']
        sub_div = locality['sub_division']
        
        # Prepare template inputs
        short_summary = category_match["category"]
        if len(complaint_text) > 30:
            # generate short summary from first 5-6 words
            words = complaint_text.split()
            short_summary = " ".join(words[:5]) + "..."
            
        template_inputs = {
            "name": name or "[Citizen Name]",
            "contact": contact or "[Contact Number]",
            "address": address or "[Citizen Address]",
            "locality_name": locality["name"],
            "landmark": landmark or "N/A",
            "pin": pin or locality["pin"],
            "complaint_text": complaint_text,
            "additional_comments": f"Note: {additional_comments}" if additional_comments else "",
            "zone_info": zone_info,
            "sub_division": sub_div,
            "agency_full_name": resolved_agency_name,
            "short_summary": short_summary,
            "road_classification_detail": road_classification_detail
        }

        try:
            draft_en = category_match["draft_template_en"].format(**template_inputs)
            draft_hi = category_match["draft_template_hi"].format(**template_inputs)
        except Exception as e:
            # Safeguard in case formatting fails
            draft_en = f"To: {resolved_agency_name}\nFrom: {name}\nComplaint: {complaint_text}"
            draft_hi = f"सेवा में: {resolved_agency_name}\nप्रेषक: {name}\nशिकायत: {complaint_text}"

        # 5. Compile Helplines & Submission Channels
        helplines_db = {
            "MCD": {"phone": "155305", "email": "grievance.hq@mcd.nic.in", "app": "MCD 311 App"},
            "DJB": {"phone": "1916", "email": "customerrelations.djb@delhi.gov.in", "app": "DJB mSeva"},
            "PWD": {"phone": "1800110093", "email": "pwd-delhi@nic.in", "app": "Delhi Government PGMS Portal"},
            "DDA": {"phone": "1800110332", "email": "ddahousing@dda.org.in", "app": "DDA 311 / PGMS"},
            "BSES Rajdhani (BRPL)": {"phone": "19123 / 011-39999707", "whatsapp": "9013599922", "email": "brpl.customercare@relianceada.com"},
            "BSES Yamuna (BYPL)": {"phone": "19122 / 011-39999808", "whatsapp": "8745999808", "email": "bypl.customercare@relianceada.com"},
            "Tata Power (TPDDL)": {"phone": "19124", "whatsapp": "9667557924", "email": "customercare@tatapower-ddl.com"},
            "NDMC": {"phone": "1533", "email": "cgrf@ndmc.gov.in", "app": "NDMC 311"},
            "Delhi Cantonment Board": {"phone": "011-25693444", "email": "ceodelh-stats@nic.in", "app": "eChhawani Portal"},
            "Traffic Police": {"phone": "1095 / 011-25844444", "whatsapp": "8750871493", "twitter": "@DelhiTrafficPol"}
        }

        # Resolve helpline for final agency (matching key in database)
        helpline_key = final_agency
        if "BSES Rajdhani" in final_agency:
            helpline_key = "BSES Rajdhani (BRPL)"
        elif "BSES Yamuna" in final_agency:
            helpline_key = "BSES Yamuna (BYPL)"
        elif "Tata Power" in final_agency:
            helpline_key = "Tata Power (TPDDL)"
        elif "Traffic" in final_agency:
            helpline_key = "Traffic Police"
        elif "MCD" in final_agency:
            helpline_key = "MCD"
        elif "PWD" in final_agency:
            helpline_key = "PWD"

        helpline_info = helplines_db.get(helpline_key, {"phone": "1031 (Delhi Govt Helpline)", "email": "pgms.delhi@gov.in"})

        # 6. Counter-Grievance Draft (Dispute Solver)
        dispute_draft = f"""To,
The Public Grievance Cell (PGMS),
Government of NCT of Delhi / Central Grievance Cell.

Subject: Escalation of Refused Grievance regarding {short_summary} at {address}
Reference/Grievance ID: [Please insert rejected grievance ID]

Dear Sir/Madam,

I am writing to register a formal dispute regarding the rejection of our grievance by {resolved_agency_name} under the claim of 'jurisdiction mismatch'.

The issue is located at: {address}, which falls under {locality['mcd_zone']} division. 
Based on administrative guidelines, this issue ({category_match['category']}) belongs to {resolved_agency_name}. 

Please review this boundary conflict, allocate resources, and prevent the citizen from being bounced between departments.

Sincerely,
{name}"""

        # Generate Unique Complaint ID
        import random
        prefix_mapping = {
            "MCD": "MCD",
            "DJB": "DJB",
            "PWD": "PWD",
            "DDA": "DDA",
            "BSES Rajdhani (BRPL)": "BRPL",
            "BSES Yamuna (BYPL)": "BYPL",
            "Tata Power (TPDDL)": "TPDDL",
            "NDMC": "NDMC",
            "Delhi Cantonment Board": "CANTT",
            "Traffic Police": "TRAF"
        }
        # Fallback search if exact match not found (e.g. dynamic full names containing MCD etc.)
        agency_prefix = "CIVIC"
        for k, v in prefix_mapping.items():
            if k in resolved_agency_name or k in final_agency:
                agency_prefix = v
                break

        date_str = datetime.now().strftime("%Y%m%d")
        random_num = random.randint(1000, 9999)
        complaint_id = f"DL-{agency_prefix}-{date_str}-{random_num}"

        return {
            "complaint_id": complaint_id,
            "resolved_agency": resolved_agency_name,
            "agency_code": final_agency,
            "category": category_match["category"],
            "score": round(match_score, 2),
            "reason": reason_summary,
            "locality_resolved": locality["name"],
            "mcd_zone": locality["mcd_zone"],
            "discom": locality["discom"],
            "documents_required": category_match["documents_required"],
            "draft_en": draft_en,
            "draft_hi": draft_hi,
            "dispute_draft": dispute_draft,
            "helpline": helpline_info
        }

agent = JurisdictionAgent(es)

# =====================================================================
# FLASK WEB ENDPOINTS & LOCAL DB STORAGE
# =====================================================================

DB_FILE = os.path.join(os.path.dirname(__file__), "complaints_db.json")

def load_db():
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Error reading db file:", e)
        return []

def save_db(data):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("Error writing db file:", e)

def mask_contact(contact):
    if not contact:
        return ""
    c_str = str(contact).strip()
    if len(c_str) >= 6:
        return c_str[:3] + "*" * (len(c_str) - 5) + c_str[-2:]
    return "***"

@app.route('/')
def home():
    # Render static frontend
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def send_static(path):
    return send_from_directory(app.static_folder, path)

# =====================================================================
# ELASTICSEARCH INDEX INSPECTION ENDPOINTS
# =====================================================================

@app.route('/api/es/status', methods=['GET'])
def api_es_status():
    """Get Elasticsearch cluster connection status."""
    return jsonify(es.get_cluster_info())

@app.route('/api/es/indices', methods=['GET'])
def api_es_list_indices():
    """List all ES indices and their document counts."""
    summary = {}
    for index_name in es.get_index_names():
        docs = es.get_index_data(index_name)
        summary[index_name] = {
            "document_count": len(docs),
            "fields": list(docs[0].keys()) if docs else []
        }
    info = es.get_cluster_info()
    return jsonify({"cluster": info, "indices": summary})

@app.route('/api/es/index/<index_name>', methods=['GET'])
def api_es_view_index(index_name):
    """View all documents in a specific ES index. 
    Usage: /api/es/index/localities  or  /api/es/index/agency_rules
    """
    docs = es.get_index_data(index_name)
    if not docs:
        return jsonify({"error": f"Index '{index_name}' not found. Available: {es.get_index_names()}"}), 404
    
    # Strip heavy template fields for readability
    clean_docs = []
    for doc in docs:
        clean = {k: v for k, v in doc.items() if not k.startswith("draft_template")}
        if "draft_template_en" in doc:
            clean["draft_template_en"] = doc["draft_template_en"][:80] + "..."
        if "draft_template_hi" in doc:
            clean["draft_template_hi"] = doc["draft_template_hi"][:80] + "..."
        clean_docs.append(clean)
    
    return jsonify({
        "index": index_name,
        "total_documents": len(docs),
        "es_mode": es.get_cluster_info()["mode"],
        "documents": clean_docs
    })

@app.route('/api/es/search/<index_name>', methods=['GET'])
def api_es_search(index_name):
    """Run a text search against any ES index.
    Usage: /api/es/search/localities?q=rohini
           /api/es/search/agency_rules?q=sewer+overflow
    """
    if index_name not in es.get_index_names():
        return jsonify({"error": f"Index '{index_name}' not found."}), 404
    
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "Provide a search query via ?q=your+search+text"}), 400
    
    # Build appropriate query based on index
    if index_name == "localities":
        query = {
            "query": {
                "bool": {
                    "should": [
                        {"term": {"pin": q}},
                        {"multi_match": {"query": q, "fields": ["name", "sub_division"]}}
                    ]
                }
            }
        }
    elif index_name == "agency_rules":
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"multi_match": {"query": q, "fields": ["keywords^2", "category", "description"]}}
                    ]
                }
            }
        }
    else:
        query = {"query": {"multi_match": {"query": q, "fields": ["*"]}}}
    
    res = es.search(index_name, query)
    
    # Clean results for readability
    hits = []
    for hit in res["hits"]["hits"]:
        source = {k: v for k, v in hit["_source"].items() if not k.startswith("draft_template")}
        hits.append({
            "id": hit["_id"],
            "score": hit["_score"],
            "source": source
        })
    
    return jsonify({
        "index": index_name,
        "query_text": q,
        "es_mode": es.get_cluster_info()["mode"],
        "total_hits": res["hits"]["total"]["value"],
        "max_score": res["hits"]["max_score"],
        "results": hits
    })

@app.route('/api/es/resolve-pin/<pin>', methods=['GET'])
def api_es_resolve_pin(pin):
    """Resolve a PIN code using the full pipeline (Public API → ES fallback).
    Usage: /api/es/resolve-pin/110085
    """
    locality, score = agent.resolve_locality(pin, "", "")
    return jsonify({
        "pin": pin,
        "resolved_locality": locality,
        "score": score,
        "source": locality.get("type", "Unknown")
    })

@app.route('/api/route-complaint', methods=['POST'])
def api_route_complaint():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No form data provided"}), 400
        
        # Get requester session headers
        user_id = request.headers.get("X-User-Id", "citizen_a")
        user_role = request.headers.get("X-User-Role", "citizen")

        # Validate mandatory fields
        required_fields = ["complaint", "name", "contact", "address", "city", "state", "pin"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        # Validate mandatory supporting document
        complaint_type = data.get("complaint_type")
        if complaint_type in ["water", "sewage", "electricity", "sanitation", "traffic", "road"]:
            if not data.get("supporting_doc"):
                return jsonify({"error": "A mandatory supporting document/photograph must be uploaded for this complaint type!"}), 400

        result = agent.route_complaint(data)
        
        # Save to DB
        db = load_db()
        complaint_entry = {
            "id": result["complaint_id"],
            "user_id": user_id,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Pending",
            "form_data": data,
            "resolved_agency": result["resolved_agency"],
            "category": result["category"],
            "reason": result["reason"],
            "draft_en": result["draft_en"],
            "draft_hi": result["draft_hi"],
            "dispute_draft": result["dispute_draft"],
            "documents_required": result["documents_required"],
            "helpline": result["helpline"],
            "supporting_doc": data.get("supporting_doc")
        }
        db.append(complaint_entry)
        save_db(db)
        
        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/complaints/<complaint_id>/update', methods=['POST'])
def api_update_complaint(complaint_id):
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No form data provided"}), 400
        
        user_id = request.headers.get("X-User-Id", "citizen_a")
        user_role = request.headers.get("X-User-Role", "citizen")

        # Validate mandatory fields
        required_fields = ["complaint", "name", "contact", "address", "city", "state", "pin"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        db = load_db()
        target_item = None
        for item in db:
            if item.get("id") == complaint_id:
                target_item = item
                break

        if not target_item:
            return jsonify({"error": "Complaint not found"}), 404

        # Enforce Ownership / RBAC update block
        if target_item.get("user_id") != user_id and user_role != "admin":
            return jsonify({"error": "Unauthorized: You can only edit your own complaints!"}), 403

        # Check if resolved
        if target_item.get("status") == "Resolved":
            return jsonify({"error": "Resolved complaints are locked and cannot be updated!"}), 400

        # Validate mandatory supporting document
        complaint_type = data.get("complaint_type")
        if complaint_type in ["water", "sewage", "electricity", "sanitation", "traffic", "road"]:
            if not data.get("supporting_doc"):
                return jsonify({"error": "A mandatory supporting document/photograph must be uploaded for this complaint type!"}), 400

        # Rerun routing with new data
        result = agent.route_complaint(data)

        # Update fields
        target_item["form_data"] = data
        target_item["resolved_agency"] = result["resolved_agency"]
        target_item["category"] = result["category"]
        target_item["reason"] = result["reason"]
        target_item["draft_en"] = result["draft_en"]
        target_item["draft_hi"] = result["draft_hi"]
        target_item["dispute_draft"] = result["dispute_draft"]
        target_item["documents_required"] = result["documents_required"]
        target_item["helpline"] = result["helpline"]
        target_item["supporting_doc"] = data.get("supporting_doc")

        save_db(db)
        
        # Overwrite result ID with original to maintain consistency
        result["complaint_id"] = complaint_id
        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/complaints', methods=['GET'])
def api_get_complaints():
    user_id = request.headers.get("X-User-Id", "citizen_a")
    user_role = request.headers.get("X-User-Role", "citizen")
    
    db = load_db()
    filtered_db = []
    
    for item in db:
        item_user_id = item.get("user_id", "citizen_a")
        
        # Isolation: Admin gets all, Citizen gets only their own
        if user_role == "admin" or item_user_id == user_id:
            # Create a copy to prevent modifying the cached DB in memory
            item_copy = json.loads(json.dumps(item))
            
            # Mask sensitive contact number if requester is NOT admin and NOT owner
            # (Note: citizens only fetch their own, but this acts as double protection)
            if user_role != "admin" and item_user_id != user_id:
                if "form_data" in item_copy and "contact" in item_copy["form_data"]:
                    item_copy["form_data"]["contact"] = mask_contact(item_copy["form_data"]["contact"])
            
            # For lists shown to Admin, we also show unmasked contacts since they are administrators.
            filtered_db.append(item_copy)
            
    return jsonify(filtered_db)

@app.route('/api/complaints/<complaint_id>/toggle-status', methods=['POST'])
def api_toggle_status(complaint_id):
    user_role = request.headers.get("X-User-Role", "citizen")
    
    # Authorize: only Admin can toggle resolution status
    if user_role != "admin":
        return jsonify({"error": "Unauthorized: Only administrators can update grievance resolution status!"}), 403

    db = load_db()
    for item in db:
        if item.get("id") == complaint_id:
            current_status = item.get("status", "Pending")
            item["status"] = "Resolved" if current_status == "Pending" else "Pending"
            save_db(db)
            return jsonify({"success": True, "new_status": item["status"]})
    return jsonify({"error": "Complaint not found"}), 404

@app.route('/api/localities', methods=['GET'])
def api_localities():
    # Return available localities for user reference
    localities = [{"name": loc["name"], "pin": loc["pin"]} for loc in localities_data]
    return jsonify(localities)

if __name__ == '__main__':
    print("--------------------------------------------------")
    print("Delhi Civic Services Navigator Server running...")
    print("Open http://127.0.0.1:5000 in your browser.")
    print("--------------------------------------------------")
    app.run(debug=True, host='127.0.0.1', port=5000)
