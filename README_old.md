# 🏛️ Delhi Civic Services Navigator

An intelligent civic grievance routing system for the citizens of Delhi. It uses **Elasticsearch-based NLP scoring** and **live India Post public data** to automatically identify the correct government agency for any complaint — resolving complex jurisdictional overlaps between MCD, DJB, PWD, DDA, DISCOMs, NDMC, Traffic Police, and the Delhi Cantonment Board.

## ✨ Key Features

- **Smart Agency Routing**: Automatically maps complaints to the correct Delhi government department using BM25/TF-IDF text scoring across categorized rule indices.
- **Live Public Data Integration**: Queries the India Post Directory API (`api.postalpincode.in`) in real-time for accurate locality resolution from PIN codes.
- **Jurisdictional Overlap Resolution**: Handles complex boundary conflicts (e.g., MCD vs PWD for roads, DJB vs MCD for drains, dynamic DISCOM routing based on locality).
- **Bilingual Draft Letters**: Auto-generates formal complaint letters in both English and Hindi, pre-addressed to the correct officer.
- **Dispute Escalation Drafts**: Generates counter-grievance letters for PGMS escalation if the agency rejects the complaint.
- **Role-Based Access Control (RBAC)**: Citizens only see their own complaints; Admin mode reveals all records.
- **Sensitive Data Masking**: Contact numbers are automatically masked for non-owners.
- **Supporting Document Upload**: Mandatory file uploads (image/PDF) for specific complaint types, stored as Base64.
- **Complaint Lifecycle Management**: Edit pending complaints, lock resolved ones, and reopen if needed.

## 🛠️ Tech Stack

- **Backend**: Python (Flask)
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Search Engine**: Elasticsearch DSL Simulator (BM25 scoring, bool queries, multi_match with field boosting)
- **Public API**: India Post PIN Code Directory
- **Storage**: JSON file-based persistent database

## 🚀 Quick Start

```bash
# Install dependencies
pip install flask

# Run the server
python server.py

# Open in browser
# http://127.0.0.1:5000
```

## 🔐 Access Modes

| Mode | URL | Description |
|------|-----|-------------|
| Citizen | `http://127.0.0.1:5000` | Default. See only your own complaints. |
| Admin | `http://127.0.0.1:5000/?role=admin` | See all complaints, unmasked contacts, resolve complaints. |

## 📂 Project Structure

```
delhi-civic-services-navigator/
├── server.py              # Flask backend + ES simulator + routing agent
├── complaints_db.json     # Persistent complaint storage
├── static/
│   ├── index.html         # Main UI page
│   ├── style.css          # Premium dark-theme styling
│   └── app.js             # Frontend logic, RBAC, file uploads
└── README.md
```

## 🏢 Supported Agencies

| Agency | Jurisdiction |
|--------|-------------|
| Delhi Jal Board (DJB) | Water supply, pipelines, sewage |
| Municipal Corporation of Delhi (MCD) | Colony roads, garbage, encroachments |
| Public Works Department (PWD) | Arterial roads, flyovers, highways |
| Delhi Development Authority (DDA) | DDA flats, parks, vacant land |
| BSES Rajdhani (BRPL) | Electricity — South & West Delhi |
| BSES Yamuna (BYPL) | Electricity — East & Central Delhi |
| Tata Power (TPDDL) | Electricity — North & Northwest Delhi |
| NDMC | All services — Lutyens' Delhi |
| Delhi Cantonment Board | All services — Cantonment area |
| Delhi Traffic Police | Traffic violations, signals, parking |

## 📜 License

MIT License
