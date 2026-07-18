document.addEventListener('DOMContentLoaded', () => {
    initUserSession();
    initLocalities();
    initDropdownHandler();
    initFileUploader();
    initFormHandler();
    initFilterHandler();
    loadComplaints();
});

// Cache for loaded complaints
let complaintsCache = [];
let editingComplaintId = null;

// Dynamic Fields based on Category Selection
const dynamicFieldsHtml = {
    water: `
        <div class="dynamic-field-row">
            <div class="form-group">
                <label for="water_kno">Water Connection K.No (10 Digits) <span class="required">*</span></label>
                <input type="text" id="water_kno" name="water_kno" placeholder="e.g. 2938471049" required maxlength="10">
                <div class="field-hint">Mandatory: Your K.No is found on your DJB bill. Photos of leak/contamination are recommended.</div>
            </div>
        </div>
    `,
    sewage: `
        <div class="dynamic-field-row">
            <div class="field-hint" style="color: var(--accent); display: flex; gap: 0.5rem; align-items: flex-start;">
                <i class="fa-solid fa-camera" style="margin-top: 0.25rem;"></i>
                <span><strong>Mandatory Document:</strong> A clear, close-up photograph of the overflowing sewer or open manhole showing the street context is required to file a sewer complaint.</span>
            </div>
        </div>
    `,
    electricity: `
        <div class="dynamic-field-row">
            <div class="form-group">
                <label for="electricity_ca">Consumer Account (CA) Number (9 Digits) <span class="required">*</span></label>
                <input type="text" id="electricity_ca" name="electricity_ca" placeholder="e.g. 100293847" required maxlength="9">
                <div class="field-hint">Mandatory: Required to file with BSES/Tata Power. Ensure you have a copy of the bill.</div>
            </div>
        </div>
    `,
    sanitation: `
        <div class="dynamic-field-row">
            <div class="field-hint" style="color: var(--accent); display: flex; gap: 0.5rem; align-items: flex-start;">
                <i class="fa-solid fa-camera" style="margin-top: 0.25rem;"></i>
                <span><strong>Mandatory Document:</strong> Geotagged photos of the garbage pile / uncleared dumpsite (essential for MCD 311 app submission) are required.</span>
            </div>
        </div>
    `,
    road: `
        <div class="dynamic-field-row">
            <div class="form-group" style="flex-direction: row; align-items: center; gap: 0.5rem;">
                <input type="checkbox" id="is_main_road" name="is_main_road" style="width: 18px; height: 18px; margin: 0; cursor: pointer;">
                <label for="is_main_road" style="margin: 0; cursor: pointer; font-weight: 600;">Is this a main arterial road/flyover (width >= 60 feet) with bus routes?</label>
            </div>
            <div class="field-hint">Checking this will route the issue to PWD instead of MCD. Include photo proof of pothole.</div>
        </div>
    `,
    traffic: `
        <div class="dynamic-field-row">
            <div class="form-group">
                <label for="vehicle_no">Vehicle Number (If reporting illegal parking / wrong way)</label>
                <input type="text" id="vehicle_no" name="vehicle_no" placeholder="e.g. DL 3C AY 4821">
                <div class="field-hint">Mandatory Document: Photograph showing vehicle license plate. Wrong way driving must have timestamp.</div>
            </div>
        </div>
    `,
    encroachment: `
        <div class="dynamic-field-row">
            <div class="form-group">
                <label for="encroachment_details">Encroached Property / Plot Number (If known)</label>
                <input type="text" id="encroachment_details" name="encroachment_details" placeholder="e.g. House No. 24, Block C-3">
                <div class="field-hint">Mandatory Document: Recent photographs of unauthorized building structure or public path blockage.</div>
            </div>
        </div>
    `
};

// Listen to Dropdown Selection changes
function initDropdownHandler() {
    const dropdown = document.getElementById('complaint_type');
    const container = document.getElementById('dynamicFieldsContainer');
    const docGroup = document.getElementById('supportingDocGroup');

    dropdown.addEventListener('change', () => {
        const type = dropdown.value;
        if (dynamicFieldsHtml[type]) {
            container.innerHTML = dynamicFieldsHtml[type];
            container.classList.remove('hidden');
        } else {
            container.innerHTML = '';
            container.classList.add('hidden');
        }

        // Show/hide supporting doc group for mandatory types
        const mandatoryTypes = ["water", "sewage", "electricity", "sanitation", "traffic", "road"];
        if (mandatoryTypes.includes(type)) {
            docGroup.classList.remove('hidden');
        } else {
            docGroup.classList.add('hidden');
            // reset file uploader state
            document.getElementById('supporting_doc').value = '';
            document.getElementById('supporting_doc_data').value = '';
            document.getElementById('fileUploadLabelText').innerHTML = `
                <i class="fa-solid fa-cloud-arrow-up"></i>
                <span>Click or Drag to Upload proof (Image/PDF)</span>
                <span class="field-hint">Mandatory for this complaint type</span>
            `;
        }
    });
}

// Load test localities on startup
async function initLocalities() {
    try {
        const response = await fetch('/api/localities');
        if (!response.ok) throw new Error("Failed to fetch localities");
        
        const localities = await response.json();
        const container = document.getElementById('localityTags');
        container.innerHTML = '';

        localities.forEach(loc => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'tag-btn';
            btn.innerHTML = `<i class="fa-solid fa-location-pin"></i> ${loc.name} (${loc.pin})`;
            btn.addEventListener('click', () => {
                document.getElementById('pin').value = loc.pin;
                const addrInput = document.getElementById('address');
                if (!addrInput.value) {
                    addrInput.value = `${loc.name}, Delhi`;
                } else if (!addrInput.value.includes(loc.name)) {
                    addrInput.value += `, ${loc.name}`;
                }
                
                // Spark micro-animation
                btn.style.transform = 'scale(0.95)';
                setTimeout(() => btn.style.transform = 'scale(1)', 100);
            });
            container.appendChild(btn);
        });
    } catch (err) {
        console.error("Localities error:", err);
        const container = document.getElementById('localityTags');
        container.innerHTML = '<span class="field-hint">Error loading test localities. Server offline?</span>';
    }
}

// Form submit handler
function initFormHandler() {
    const form = document.getElementById('complaintForm');
    const submitBtn = document.getElementById('submitBtn');
    
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        // Extract basic values
        const payload = {
            complaint_type: document.getElementById('complaint_type').value,
            complaint: document.getElementById('complaint').value,
            name: document.getElementById('name').value,
            contact: document.getElementById('contact').value,
            address: document.getElementById('address').value,
            city: document.getElementById('city').value,
            state: document.getElementById('state').value,
            pin: document.getElementById('pin').value,
            landmark: document.getElementById('landmark').value,
            additional_comments: document.getElementById('additional_comments').value
        };

        // Extract issue-specific values
        const waterKno = document.getElementById('water_kno');
        const electricityCa = document.getElementById('electricity_ca');
        const isMainRoad = document.getElementById('is_main_road');
        const vehicleNo = document.getElementById('vehicle_no');
        const encroachmentDetails = document.getElementById('encroachment_details');

        if (waterKno) payload.water_kno = waterKno.value;
        if (electricityCa) payload.electricity_ca = electricityCa.value;
        if (isMainRoad) payload.is_main_road = isMainRoad.checked;
        if (vehicleNo) payload.vehicle_no = vehicleNo.value;
        if (encroachmentDetails) payload.encroachment_details = encroachmentDetails.value;

        // Check if supporting document is mandatory and present
        const mandatoryTypes = ["water", "sewage", "electricity", "sanitation", "traffic", "road"];
        const docData = document.getElementById('supporting_doc_data').value;
        if (mandatoryTypes.includes(payload.complaint_type) && !docData) {
            alert('Error: A supporting document/photograph is mandatory to submit this complaint type. Please upload a file first.');
            return;
        }
        
        payload.supporting_doc = docData;

        // Show loading state in button
        const originalBtnContent = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing...';

        // Determine URL (create new vs update unresolved)
        const url = editingComplaintId ? 
            `/api/complaints/${editingComplaintId}/update` : 
            '/api/route-complaint';

        const headers = {
            'Content-Type': 'application/json',
            ...getSessionHeaders()
        };

        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || "Server error occurred");
            }

            const data = await response.json();
            renderResults(data);
            
            // Clear edit state if we were editing
            if (editingComplaintId) {
                cancelEdit();
            } else {
                form.reset();
                const container = document.getElementById('dynamicFieldsContainer');
                container.innerHTML = '';
                container.classList.add('hidden');
                
                // Hide and clear supporting document uploader
                const docGroup = document.getElementById('supportingDocGroup');
                docGroup.classList.add('hidden');
                document.getElementById('supporting_doc').value = '';
                document.getElementById('supporting_doc_data').value = '';
                document.getElementById('fileUploadLabelText').innerHTML = `
                    <i class="fa-solid fa-cloud-arrow-up"></i>
                    <span>Click or Drag to Upload proof (Image/PDF)</span>
                    <span class="field-hint">Mandatory for this complaint type</span>
                `;
            }
            
            // Reload filed complaints history
            loadComplaints();

        } catch (err) {
            alert(`Error: ${err.message}`);
        } finally {
            // Restore button state
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalBtnContent;
        }
    });
}

// Initialize filter dropdown change listener
function initFilterHandler() {
    const filter = document.getElementById('agencyFilter');
    filter.addEventListener('change', () => {
        renderComplaintsTable();
    });
}

// Fetch previous complaints history
async function loadComplaints() {
    try {
        const response = await fetch('/api/complaints', {
            headers: getSessionHeaders()
        });
        if (!response.ok) throw new Error("Failed to fetch complaints history");

        const complaints = await response.json();
        
        // Auto-migration: if citizen has 0 complaints and is using an old random ID,
        // migrate them to the seeded demo user to see the pre-populated history.
        if (complaints.length === 0 && localStorage.getItem('civic_user_id') !== 'usr_delhi_citizen_demo' && localStorage.getItem('civic_user_role') !== 'admin') {
            localStorage.setItem('civic_user_id', 'usr_delhi_citizen_demo');
            return loadComplaints(); // retry loading with demo ID
        }

        complaintsCache = complaints; // Cache details locally

        // Render table
        renderComplaintsTable();

    } catch (err) {
        console.error("History error:", err);
    }
}

// Check if a complaint matches the selected filter category
function matchesFilter(complaint, filterValue) {
    if (filterValue === 'all') return true;
    
    const agencyLower = (complaint.resolved_agency || '').toLowerCase();
    const idLower = (complaint.id || '').toLowerCase();
    
    switch (filterValue) {
        case 'mcd':
            return idLower.includes('mcd') || agencyLower.includes('mcd') || agencyLower.includes('municipal corporation');
        case 'djb':
            return idLower.includes('djb') || agencyLower.includes('djb') || agencyLower.includes('jal board');
        case 'pwd':
            return idLower.includes('pwd') || agencyLower.includes('pwd') || agencyLower.includes('public works');
        case 'discom':
            return idLower.includes('brpl') || idLower.includes('bypl') || idLower.includes('tpddl') || 
                   agencyLower.includes('bses') || agencyLower.includes('tata power') || agencyLower.includes('discom');
        case 'traffic':
            return idLower.includes('traf') || agencyLower.includes('traffic');
        case 'dda':
            return idLower.includes('dda') || agencyLower.includes('dda') || agencyLower.includes('development authority');
        case 'ndmc':
            return idLower.includes('ndmc') || agencyLower.includes('ndmc') || agencyLower.includes('new delhi municipal');
        case 'cantonment':
            return idLower.includes('cantt') || agencyLower.includes('cantonment');
        default:
            return true;
    }
}

// Render complaints table using cached database entries and active filter
function renderComplaintsTable() {
    const filterValue = document.getElementById('agencyFilter').value;
    const placeholder = document.getElementById('historyPlaceholder');
    const tableContainer = document.getElementById('historyTableContainer');
    const listBody = document.getElementById('historyList');

    if (complaintsCache.length === 0) {
        placeholder.innerHTML = '<p><i class="fa-solid fa-folder-open"></i> No complaints filed yet.</p>';
        placeholder.classList.remove('hidden');
        tableContainer.classList.add('hidden');
        return;
    }

    // Filter Cache items
    const filteredComplaints = complaintsCache.filter(c => matchesFilter(c, filterValue));

    if (filteredComplaints.length === 0) {
        placeholder.innerHTML = `<p><i class="fa-solid fa-filter"></i> No complaints found matching the selected agency.</p>`;
        placeholder.classList.remove('hidden');
        tableContainer.classList.add('hidden');
        return;
    }

    placeholder.classList.add('hidden');
    tableContainer.classList.remove('hidden');
    listBody.innerHTML = '';

    // Render entries (reversed so newest are first)
    [...filteredComplaints].reverse().forEach(c => {
        const tr = document.createElement('tr');
        
        // Status badge class
        const isResolved = c.status === 'Resolved';
        const statusClass = isResolved ? 'status-resolved' : 'status-pending';
        const statusIcon = isResolved ? 
            '<i class="fa-solid fa-circle-check"></i>' : 
            '<i class="fa-solid fa-circle-notch fa-spin"></i>';
        
        const isAdmin = getSessionHeaders()['X-User-Role'] === 'admin';
        let actionButtonsHtml = '';
        
        if (isResolved) {
            actionButtonsHtml = `
                <button class="btn btn-sm btn-outline" disabled style="opacity: 0.5; cursor: not-allowed; min-width: 120px;">
                    <i class="fa-solid fa-lock"></i> Locked
                </button>
            `;
        } else {
            actionButtonsHtml = `
                <button class="btn btn-sm btn-outline" onclick="startEdit('${c.id}')">
                    <i class="fa-solid fa-pen-to-square"></i> Edit
                </button>
            `;
            if (isAdmin) {
                actionButtonsHtml += `
                    <button class="btn btn-sm btn-outline" onclick="toggleStatus('${c.id}', this)" style="min-width: 120px; border-color: var(--primary); color: var(--primary);">
                        <i class="fa-solid fa-check"></i> Mark Resolved
                    </button>
                `;
            }
        }

        tr.innerHTML = `
            <td>
                <span class="complaint-id-link" onclick="showComplaintDetailsModal('${c.id}')">${c.id}</span>
            </td>
            <td>${c.date}</td>
            <td><strong>${c.category}</strong></td>
            <td>${c.resolved_agency}</td>
            <td>${c.form_data.name}</td>
            <td>
                <span class="status-pill ${statusClass}">
                    ${statusIcon} ${c.status}
                </span>
            </td>
            <td>
                <div style="display: flex; gap: 0.5rem;">
                    ${actionButtonsHtml}
                </div>
            </td>
        `;
        listBody.appendChild(tr);
    });
}

// Toggle resolution status of complaint
async function toggleStatus(id, btn) {
    const originalBtn = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    
    try {
        const response = await fetch(`/api/complaints/${id}/toggle-status`, {
            method: 'POST',
            headers: getSessionHeaders()
        });
        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.error || "Failed to toggle status");
        }
        
        // Reload table
        await loadComplaints();
        
        // If the currently viewed complaint in the dashboard is the one updated,
        // we can reload or show updated tags if needed.
    } catch (err) {
        alert(err.message);
    } finally {
        btn.disabled = false;
    }
}

// Load cached complaint details back into the dashboard results when ID is clicked
function loadCachedComplaint(id) {
    const complaint = complaintsCache.find(c => c.id === id);
    if (!complaint) return;

    // Call renderResults using stored data fields
    const data = {
        complaint_id: complaint.id,
        resolved_agency: complaint.resolved_agency,
        agency_code: complaint.form_data.complaint_type,
        category: complaint.category,
        score: 10.0, // preset mockup score
        reason: complaint.reason,
        locality_resolved: complaint.form_data.address.split(',').pop().trim(),
        mcd_zone: complaint.form_data.pin, // fallback display
        discom: "DISCOM", // fallback display
        documents_required: complaint.documents_required,
        draft_en: complaint.draft_en,
        draft_hi: complaint.draft_hi,
        dispute_draft: complaint.dispute_draft,
        helpline: complaint.helpline
    };

    // Attempt to enrich locality specs if they exist in cache
    if (complaint.reason.includes('Zone')) {
        const zoneMatch = complaint.reason.match(/([a-zA-Z\s]+)\sZone/);
        if (zoneMatch) data.mcd_zone = zoneMatch[1];
    }
    if (complaint.reason.includes('BSES') || complaint.reason.includes('Tata')) {
        const discomMatch = complaint.reason.match(/(BSES Rajdhani \(BRPL\)|BSES Yamuna \(BYPL\)|Tata Power \(TPDDL\))/);
        if (discomMatch) data.discom = discomMatch[1];
    }

    renderResults(data);
}

// Render dynamic agent outputs
function renderResults(data) {
    // Hide placeholder, show content
    document.getElementById('outputPlaceholder').classList.add('hidden');
    const resultContent = document.getElementById('resultContent');
    resultContent.classList.remove('hidden');

    // Scroll output panel into view (smooth transition)
    document.getElementById('outputPanel').scrollIntoView({ behavior: 'smooth' });

    // 1. Core routing card details (with Unique ID display)
    document.getElementById('resolvedAgency').innerHTML = `
        <div style="font-size: 0.8rem; color: var(--text-muted); font-family: monospace; margin-bottom: 0.2rem;">
            ID: <span style="color: var(--primary); font-weight: bold;">${data.complaint_id}</span>
        </div>
        ${data.resolved_agency}
    `;
    document.getElementById('resolvedCategory').textContent = data.category;
    document.getElementById('esScore').textContent = data.score;
    document.getElementById('routingReason').textContent = data.reason;

    // 2. Geo/Subdivision specifications
    document.getElementById('resLocality').textContent = data.locality_resolved;
    document.getElementById('resMcdZone').textContent = data.mcd_zone.includes('Zone') ? data.mcd_zone : `${data.mcd_zone} Zone`;
    document.getElementById('resDiscom').textContent = data.discom;

    // Set matching icon based on resolved agency
    const iconWrapper = document.getElementById('agencyIcon');
    const code = (data.agency_code || '').toLowerCase();
    const agencyNameLower = data.resolved_agency.toLowerCase();
    
    if (agencyNameLower.includes('mcd') || code.includes('mcd')) {
        iconWrapper.innerHTML = '<i class="fa-solid fa-building-flag"></i>';
        iconWrapper.style.color = '#10b981';
        iconWrapper.style.background = 'rgba(16, 185, 129, 0.1)';
    } else if (agencyNameLower.includes('jal') || code.includes('djb') || agencyNameLower.includes('djb')) {
        iconWrapper.innerHTML = '<i class="fa-solid fa-faucet-drip"></i>';
        iconWrapper.style.color = '#3b82f6';
        iconWrapper.style.background = 'rgba(59, 130, 246, 0.1)';
    } else if (agencyNameLower.includes('public works') || code.includes('pwd') || agencyNameLower.includes('pwd')) {
        iconWrapper.innerHTML = '<i class="fa-solid fa-road"></i>';
        iconWrapper.style.color = '#f59e0b';
        iconWrapper.style.background = 'rgba(245, 158, 11, 0.1)';
    } else if (agencyNameLower.includes('traffic') || code.includes('traffic')) {
        iconWrapper.innerHTML = '<i class="fa-solid fa-traffic-light"></i>';
        iconWrapper.style.color = '#ef4444';
        iconWrapper.style.background = 'rgba(239, 68, 68, 0.1)';
    } else if (agencyNameLower.includes('bses') || agencyNameLower.includes('tata') || code.includes('electricity') || code.includes('power')) {
        iconWrapper.innerHTML = '<i class="fa-solid fa-bolt"></i>';
        iconWrapper.style.color = '#ec4899';
        iconWrapper.style.background = 'rgba(236, 72, 153, 0.1)';
    } else {
        iconWrapper.innerHTML = '<i class="fa-solid fa-circle-nodes"></i>';
        iconWrapper.style.color = '#a855f7';
        iconWrapper.style.background = 'rgba(168, 85, 247, 0.1)';
    }

    // 3. Set drafts
    document.getElementById('draftEnText').textContent = data.draft_en;
    document.getElementById('draftHiText').textContent = data.draft_hi;
    document.getElementById('disputeText').textContent = data.dispute_draft;

    // Reset tabs to default (English)
    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => btn.classList.remove('active'));
    tabBtns[0].classList.add('active');
    
    const tabContents = document.querySelectorAll('.tab-content');
    tabContents.forEach(c => c.classList.remove('active'));
    document.getElementById('tabEnglish').classList.add('active');

    // 4. Set required documents
    const docChecklist = document.getElementById('docChecklist');
    docChecklist.innerHTML = '';
    
    data.documents_required.forEach((doc, idx) => {
        const li = document.createElement('li');
        const id = `doc_${idx}`;
        
        li.innerHTML = `
            <input type="checkbox" id="${id}">
            <label for="${id}">${doc}</label>
        `;
        
        // Add line-through listener
        const checkbox = li.querySelector('input');
        checkbox.addEventListener('change', () => {
            const label = li.querySelector('label');
            if (checkbox.checked) {
                label.style.textDecoration = 'line-through';
                label.style.color = 'var(--text-muted)';
            } else {
                label.style.textDecoration = 'none';
                label.style.color = 'var(--text-secondary)';
            }
        });
        docChecklist.appendChild(li);
    });

    // 5. Populate submission channels & helpline
    const helplineInfo = document.getElementById('helplineInfo');
    helplineInfo.innerHTML = '';

    // Main Helpline phone number
    if (data.helpline.phone) {
        helplineInfo.appendChild(createContactItem('fa-phone', 'Call Toll-Free Helpline', data.helpline.phone));
    }
    
    // WhatsApp helpline (if available)
    if (data.helpline.whatsapp) {
        const valueDiv = document.createElement('div');
        valueDiv.className = 'contact-value';
        
        const link = document.createElement('a');
        link.href = `https://wa.me/91${data.helpline.whatsapp.replace(/[-\s]/g, '')}`;
        link.target = '_blank';
        link.style.color = '#10b981';
        link.style.textDecoration = 'underline';
        link.innerHTML = `${data.helpline.whatsapp} <i class="fa-solid fa-arrow-up-right-from-square" style="font-size:0.7rem;"></i>`;
        
        const item = createContactItem('fa-whatsapp', 'WhatsApp Support', '');
        item.querySelector('.contact-info').appendChild(link);
        helplineInfo.appendChild(item);
    }

    // Email channel
    if (data.helpline.email) {
        helplineInfo.appendChild(createContactItem('fa-envelope', 'Official Email Channel', data.helpline.email));
    }

    // Portal / Mobile App
    if (data.helpline.app) {
        helplineInfo.appendChild(createContactItem('fa-mobile-screen-button', 'Mobile App / Web Portal', data.helpline.app));
    }
}

// Helper to construct a contact detail row
function createContactItem(iconClass, label, value) {
    const item = document.createElement('div');
    item.className = 'contact-item';
    
    item.innerHTML = `
        <div class="contact-icon">
            <i class="fa-solid ${iconClass}"></i>
        </div>
        <div class="contact-info">
            <span class="contact-label">${label}</span>
            ${value ? `<span class="contact-value">${value}</span>` : ''}
        </div>
    `;
    return item;
}

// Tab Switching
function switchTab(evt, tabId) {
    const tabContents = document.querySelectorAll('.tab-content');
    tabContents.forEach(content => content.classList.remove('active'));

    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => btn.classList.remove('active'));

    document.getElementById(tabId).classList.add('active');
    evt.currentTarget.classList.add('active');
}

// Copy Action
function copyText(elementId, button) {
    const text = document.getElementById(elementId).textContent;
    navigator.clipboard.writeText(text).then(() => {
        const originalText = button.innerHTML;
        button.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
        button.style.borderColor = 'var(--primary)';
        button.style.color = 'var(--primary)';
        setTimeout(() => {
            button.innerHTML = originalText;
            button.style.borderColor = 'var(--border-color)';
            button.style.color = 'var(--text-secondary)';
        }, 2000);
    }).catch(err => {
        alert('Could not copy text: ', err);
    });
}

// Download Action
function downloadText(elementId, filename) {
    const text = document.getElementById(elementId).textContent;
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
}

// Print Action
function printText(elementId) {
    window.print();
}

// Show complaint details in a modal
function showComplaintDetailsModal(id) {
    const complaint = complaintsCache.find(c => c.id === id);
    if (!complaint) return;

    const modal = document.getElementById('detailsModal');
    const body = document.getElementById('modalBody');

    // Build details grid
    const f = complaint.form_data;
    
    // Dynamic fields display
    let dynamicDetails = '';
    if (f.water_kno) {
        dynamicDetails = `<div class="detail-field"><span class="detail-label">Water Connection K.No</span><span class="detail-value">${f.water_kno}</span></div>`;
    } else if (f.electricity_ca) {
        dynamicDetails = `<div class="detail-field"><span class="detail-label">Electricity CA Number</span><span class="detail-value">${f.electricity_ca}</span></div>`;
    } else if (f.is_main_road !== undefined) {
        dynamicDetails = `<div class="detail-field"><span class="detail-label">Is PWD Main Road?</span><span class="detail-value">${f.is_main_road ? 'Yes (PWD Jurisdiction)' : 'No (MCD Colony Road)'}</span></div>`;
    } else if (f.vehicle_no) {
        dynamicDetails = `<div class="detail-field"><span class="detail-label">Reported Vehicle No</span><span class="detail-value">${f.vehicle_no}</span></div>`;
    } else if (f.encroachment_details) {
        dynamicDetails = `<div class="detail-field"><span class="detail-label">Encroached Plot/Property No</span><span class="detail-value">${f.encroachment_details}</span></div>`;
    }

    // Attached Document preview block
    let docSection = '';
    if (complaint.supporting_doc) {
        const isPdf = complaint.supporting_doc.startsWith('data:application/pdf');
        if (isPdf) {
            docSection = `
                <div class="detail-section">
                    <h4><i class="fa-solid fa-file-pdf"></i> Attached Document Proof</h4>
                    <div class="doc-preview-container">
                        <span><i class="fa-solid fa-file-pdf" style="color: #ef4444; font-size: 1.2rem;"></i> PDF Document uploaded</span>
                        <a href="${complaint.supporting_doc}" download="Supporting_Document_${complaint.id}.pdf" class="btn btn-sm btn-outline"><i class="fa-solid fa-download"></i> Download PDF</a>
                    </div>
                </div>
            `;
        } else {
            docSection = `
                <div class="detail-section">
                    <h4><i class="fa-solid fa-image"></i> Attached Photograph / Proof</h4>
                    <div style="background: #080c14; padding: 1rem; border-radius: var(--radius-sm); border: 1px solid var(--border-color); text-align: center;">
                        <img src="${complaint.supporting_doc}" class="doc-preview-thumb" alt="Uploaded Document Proof">
                    </div>
                </div>
            `;
        }
    }

    body.innerHTML = `
        <div class="detail-grid">
            <div class="detail-field">
                <span class="detail-label">Complaint ID</span>
                <span class="detail-value" style="color: var(--primary); font-family: monospace;">${complaint.id}</span>
            </div>
            <div class="detail-field">
                <span class="detail-label">Date Filed</span>
                <span class="detail-value">${complaint.date}</span>
            </div>
            <div class="detail-field">
                <span class="detail-label">Citizen Name</span>
                <span class="detail-value">${f.name}</span>
            </div>
            <div class="detail-field">
                <span class="detail-label">Contact Number</span>
                <span class="detail-value">${f.contact}</span>
            </div>
            <div class="detail-field">
                <span class="detail-label">Address</span>
                <span class="detail-value">${f.address}, ${f.city}, ${f.state} - ${f.pin}</span>
            </div>
            <div class="detail-field">
                <span class="detail-label">Landmark</span>
                <span class="detail-value">${f.landmark || 'N/A'}</span>
            </div>
            <div class="detail-field">
                <span class="detail-label">Grievance Status</span>
                <span class="detail-value">
                    <span class="status-pill ${complaint.status === 'Resolved' ? 'status-resolved' : 'status-pending'}">
                        ${complaint.status}
                    </span>
                </span>
            </div>
            ${dynamicDetails}
        </div>

        <div class="detail-section">
            <h4><i class="fa-solid fa-align-left"></i> Original Complaint Description</h4>
            <div class="detail-box">${f.complaint}</div>
        </div>

        ${f.additional_comments ? `
        <div class="detail-section">
            <h4><i class="fa-solid fa-comment-dots"></i> Additional Comments</h4>
            <div class="detail-box">${f.additional_comments}</div>
        </div>
        ` : ''}

        ${docSection}

        <div class="detail-section">
            <h4><i class="fa-solid fa-map-location-dot"></i> Assigned Agency & Routing Logic</h4>
            <div class="detail-box" style="border-left: 3px solid var(--primary);">
                <strong>Assigned to:</strong> ${complaint.resolved_agency}<br>
                <strong>Routing Category:</strong> ${complaint.category}<br><br>
                <strong>Routing Explanation:</strong> ${complaint.reason}
            </div>
        </div>

        <div class="detail-section">
            <h4><i class="fa-solid fa-file-contract"></i> Drafted English Letter</h4>
            <pre class="detail-box detail-box-code">${complaint.draft_en}</pre>
        </div>

        <div class="detail-section">
            <h4><i class="fa-solid fa-file-contract"></i> Drafted Hindi Letter (शिकायत का प्रारूप)</h4>
            <pre class="detail-box detail-box-code" style="font-family: var(--font-body); font-size: 0.9rem;">${complaint.draft_hi}</pre>
        </div>
    `;

    modal.classList.remove('hidden');
}

// Close the details modal
function closeModal() {
    document.getElementById('detailsModal').classList.add('hidden');
}

// Load unresolved complaint details back into form for editing
function startEdit(id) {
    const complaint = complaintsCache.find(c => c.id === id);
    if (!complaint || complaint.status === 'Resolved') return;

    const f = complaint.form_data;
    
    // Set standard fields
    document.getElementById('complaint_type').value = f.complaint_type;
    document.getElementById('complaint').value = f.complaint;
    document.getElementById('name').value = f.name;
    document.getElementById('contact').value = f.contact;
    document.getElementById('address').value = f.address;
    document.getElementById('city').value = f.city;
    document.getElementById('state').value = f.state;
    document.getElementById('pin').value = f.pin;
    document.getElementById('landmark').value = f.landmark || '';
    document.getElementById('additional_comments').value = f.additional_comments || '';

    // Trigger select dropdown change event to render dynamic inputs
    const container = document.getElementById('dynamicFieldsContainer');
    const docGroup = document.getElementById('supportingDocGroup');
    
    if (dynamicFieldsHtml[f.complaint_type]) {
        container.innerHTML = dynamicFieldsHtml[f.complaint_type];
        container.classList.remove('hidden');

        // Populate dynamic input fields once rendered in DOM
        const waterKno = document.getElementById('water_kno');
        const electricityCa = document.getElementById('electricity_ca');
        const isMainRoad = document.getElementById('is_main_road');
        const vehicleNo = document.getElementById('vehicle_no');
        const encroachmentDetails = document.getElementById('encroachment_details');

        if (waterKno && f.water_kno) waterKno.value = f.water_kno;
        if (electricityCa && f.electricity_ca) electricityCa.value = f.electricity_ca;
        if (isMainRoad && f.is_main_road !== undefined) isMainRoad.checked = f.is_main_road;
        if (vehicleNo && f.vehicle_no) vehicleNo.value = f.vehicle_no;
        if (encroachmentDetails && f.encroachment_details) encroachmentDetails.value = f.encroachment_details;
    } else {
        container.innerHTML = '';
        container.classList.add('hidden');
    }

    // Show supporting doc upload group for mandatory types
    const mandatoryTypes = ["water", "sewage", "electricity", "sanitation", "traffic", "road"];
    if (mandatoryTypes.includes(f.complaint_type)) {
        docGroup.classList.remove('hidden');
        if (complaint.supporting_doc) {
            document.getElementById('supporting_doc_data').value = complaint.supporting_doc;
            document.getElementById('fileUploadLabelText').innerHTML = `
                <i class="fa-solid fa-circle-check" style="color: var(--success);"></i>
                <span style="color: var(--success); font-weight: bold;">Document Loaded from Draft</span>
                <span class="field-hint">Click or drag to change file</span>
            `;
        }
    } else {
        docGroup.classList.add('hidden');
        document.getElementById('supporting_doc').value = '';
        document.getElementById('supporting_doc_data').value = '';
    }

    // Set editing state
    editingComplaintId = id;
    
    // Update banner
    const banner = document.getElementById('editBanner');
    document.getElementById('editComplaintId').textContent = id;
    banner.classList.remove('hidden');

    // Update submit button
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> Update Complaint`;
    submitBtn.style.background = 'var(--accent)';
    submitBtn.style.borderColor = 'var(--accent)';

    // Scroll form into view
    document.querySelector('.form-panel').scrollIntoView({ behavior: 'smooth' });
}

// Cancel the active edit state
function cancelEdit() {
    const form = document.getElementById('complaintForm');
    form.reset();

    // Hide dynamic fields
    const container = document.getElementById('dynamicFieldsContainer');
    container.innerHTML = '';
    container.classList.add('hidden');

    // Hide and clear file uploads
    const docGroup = document.getElementById('supportingDocGroup');
    docGroup.classList.add('hidden');
    document.getElementById('supporting_doc').value = '';
    document.getElementById('supporting_doc_data').value = '';
    document.getElementById('fileUploadLabelText').innerHTML = `
        <i class="fa-solid fa-cloud-arrow-up"></i>
        <span>Click or Drag to Upload proof (Image/PDF)</span>
        <span class="field-hint">Mandatory for this complaint type</span>
    `;

    // Reset editing state
    editingComplaintId = null;

    // Hide banner
    document.getElementById('editBanner').classList.add('hidden');

    // Restore submit button
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> Route & Generate Complaint`;
    submitBtn.style.background = 'var(--primary)';
    submitBtn.style.borderColor = 'var(--primary)';
}

// Get current active user headers from localStorage
function getSessionHeaders() {
    return {
        'X-User-Id': localStorage.getItem('civic_user_id') || 'usr_anonymous',
        'X-User-Role': localStorage.getItem('civic_user_role') || 'citizen'
    };
}

// Setup or retrieve persistent user session from localStorage
function initUserSession() {
    // Set default demo user ID if not present
    let civicUserId = localStorage.getItem('civic_user_id');
    if (!civicUserId) {
        civicUserId = 'usr_delhi_citizen_demo';
        localStorage.setItem('civic_user_id', civicUserId);
    }
    
    // Check for role param override in URL (e.g. ?role=admin)
    const urlParams = new URLSearchParams(window.location.search);
    const roleParam = urlParams.get('role');
    if (roleParam === 'admin') {
        localStorage.setItem('civic_user_role', 'admin');
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (roleParam === 'citizen') {
        localStorage.setItem('civic_user_role', 'citizen');
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (!localStorage.getItem('civic_user_role')) {
        localStorage.setItem('civic_user_role', 'citizen');
    }

    // Toggle admin mode banner if active
    const adminBadge = document.getElementById('adminBadge');
    if (adminBadge) {
        if (localStorage.getItem('civic_user_role') === 'admin') {
            adminBadge.classList.remove('hidden');
        } else {
            adminBadge.classList.add('hidden');
        }
    }
}

// Setup file reader and base64 loader
function initFileUploader() {
    const fileInput = document.getElementById('supporting_doc');
    const hiddenInput = document.getElementById('supporting_doc_data');
    const labelText = document.getElementById('fileUploadLabelText');

    if (!fileInput || !hiddenInput) return;

    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (file.size > 5 * 1024 * 1024) {
            alert('File size exceeds 5MB limit.');
            fileInput.value = '';
            hiddenInput.value = '';
            labelText.innerHTML = `
                <i class="fa-solid fa-cloud-arrow-up" style="color: var(--danger);"></i>
                <span style="color: var(--danger);">File exceeds 5MB limit. Try again.</span>
            `;
            return;
        }

        const reader = new FileReader();
        reader.onload = function(evt) {
            hiddenInput.value = evt.target.result;
            labelText.innerHTML = `
                <i class="fa-solid fa-circle-check" style="color: var(--success);"></i>
                <span style="color: var(--success); font-weight: bold;">File Selected: ${file.name}</span>
                <span class="field-hint">Click or drag to change file</span>
            `;
        };
        reader.readAsDataURL(file);
    });
}
