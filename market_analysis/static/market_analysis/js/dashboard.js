/**
 * Dashboard JavaScript
 * Handles modal functionality for project status management, contract details, and chart rendering
 */

/**
 * Initialize the Win/Lost doughnut chart
 * @param {string} canvasId - The ID of the canvas element
 * @param {number} winCount - Number of won projects
 * @param {number} lostCount - Number of lost projects
 */
function initWinLostChart(canvasId, winCount, lostCount) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    // Chart.js accepts either a canvas element or a 2D context. Prefer the context to avoid renderer issues.
    const ctx = canvas.getContext ? canvas.getContext('2d') : canvas;

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Won', 'Lost'],
            datasets: [{
                data: [winCount, lostCount],
                backgroundColor: [
                    '#009E73', /* teal - Win (colorblind-safe) */
                    '#CC79A7'  /* purple - Loss (colorblind-safe) */
                ],
                borderColor: [
                    '#ffffff',
                    '#ffffff'
                ],
                borderWidth: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 20,
                        usePointStyle: true,
                        font: {
                            size: 14,
                            family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'
                        }
                    }
                },
                tooltip: {
                    backgroundColor: '#24292f',
                    titleFont: {
                        size: 14
                    },
                    bodyFont: {
                        size: 13
                    },
                    padding: 12,
                    cornerRadius: 8
                }
            },
            cutout: '60%'
        }
    });
}

/**
 * Initialize the EBIT/Day horizontal bar chart
 * @param {string} canvasId - The ID of the canvas element
 * @param {Array} ebitData - Array of objects with name, ebit_day, and status properties
 */
function initEbitDayChart(canvasId, ebitData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext ? canvas.getContext('2d') : canvas;

    // ebitData may be passed as a JSON string from the template. Normalize it to an array.
    if (!ebitData) ebitData = [];
    if (typeof ebitData === 'string') {
        try {
            ebitData = JSON.parse(ebitData);
        } catch (e) {
            console.error('Unable to parse ebit_data for chart', e);
            ebitData = [];
        }
    }

    const maxLabelLength = 25;
    const projectLabels = ebitData.map(item => {
        const name = item && item.name ? String(item.name) : '';
        return name.length > maxLabelLength ? name.substring(0, maxLabelLength) + '...' : name;
    });
    const ebitValues = ebitData.map(item => {
        const v = item && (item.ebit_day ?? item.ebitDay ?? item.ebit) ? Number(item.ebit_day ?? item.ebitDay ?? item.ebit) : 0;
        return isNaN(v) ? 0 : v;
    });
    const backgroundColors = ebitData.map(item => (item && item.status === 'Won') ? '#009E73' : '#CC79A7');

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: projectLabels,
            datasets: [{
                label: 'EBIT/Day ($)',
                data: ebitValues,
                backgroundColor: backgroundColors,
                borderColor: backgroundColors,
                borderWidth: 1,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: '#24292f',
                    titleFont: {
                        size: 14
                    },
                    bodyFont: {
                        size: 13
                    },
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        label: function(context) {
                            const value = context.raw;
                            return 'EBIT/Day: $' + value.toLocaleString();
                        }
                    }
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    grid: {
                        color: '#d0d7de'
                    },
                    ticks: {
                        callback: function(value) {
                            return '$' + value.toLocaleString();
                        }
                    }
                },
                y: {
                    grid: {
                        display: false
                    }
                }
            }
        }
    });
}

/**
 * Initialize modal functionality for project status management
 */
function initStatusModal() {
    const contractModalEl = document.getElementById('contractModal');
    const contractForm = document.getElementById('contractForm');
    const modal = new bootstrap.Modal(contractModalEl, {backdrop: 'static'});
    const defaultAction = contractForm.getAttribute('action'); // contains project_id=0 placeholder

    const actualStartInput = document.getElementById('actualStart');
    const actualEndInput = document.getElementById('actualEnd');
    const computedDurationEl = document.getElementById('computedDuration');

    const statusSelectRow = document.getElementById('statusSelectRow');
    const newStatusSelect = document.getElementById('newStatusSelect');
    const submissionRow = document.getElementById('submissionRow');
    const submissionDate = document.getElementById('submissionDate');
    const awardRow = document.getElementById('awardRow');
    const awardDate = document.getElementById('awardDate');
    const contractDateRow = document.getElementById('contractDateRow');
    const contractDate = document.getElementById('contractDate');
    const lostRow = document.getElementById('lostRow');
    const lostDate = document.getElementById('lostDate');
    const competitorField = document.getElementById('competitorField');
    const competitorSelect = document.getElementById('competitorSelect');
    const contractRows = document.getElementById('contractRows');

    // track current project status for modal session (so selecting Won does NOT show contractRows)
    let modalCurrentStatus = null;

    /**
     * Parse a date string in ISO format (YYYY-MM-DD)
     * @param {string} value - Date string in ISO format
     * @returns {Date|null} - Date object or null if invalid
     */
    function parseDateISO(value) {
        if (!value) return null;
        const parts = value.split('-');
        if (parts.length !== 3) return null;
        const y = parseInt(parts[0], 10);
        const m = parseInt(parts[1], 10) - 1;
        const d = parseInt(parts[2], 10);
        return new Date(Date.UTC(y, m, d));
    }

    /**
     * Compute and display the duration between start and end dates
     */
    function computeAndShowDuration() {
        const s = actualStartInput.value;
        const e = actualEndInput.value;
        const start = parseDateISO(s);
        const end = parseDateISO(e);
        if (start && end) {
            const diffMs = end - start;
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
            computedDurationEl.textContent = (isNaN(diffDays) || diffDays < 0) ? '-' : diffDays;
        } else {
            computedDurationEl.textContent = '-';
        }
    }

    if (actualStartInput) actualStartInput.addEventListener('change', computeAndShowDuration);
    if (actualEndInput) actualEndInput.addEventListener('change', computeAndShowDuration);

    /**
     * Reset modal visibility and clear all fields
     */
    function resetModalVisibility() {
        statusSelectRow.style.display = 'none';
        submissionRow.style.display = 'none';
        awardRow.style.display = 'none';
        contractDateRow.style.display = 'none';
        lostRow.style.display = 'none';
        competitorField.style.display = 'none';
        contractRows.style.display = 'none';
        submissionDate.value = '';
        awardDate.value = '';
        contractDate.value = '';
        lostDate.value = '';
        competitorSelect.value = '';
        newStatusSelect.innerHTML = '<option value="">Select status</option>';
        modalCurrentStatus = null;
    }

    /**
     * Append an option element to a select element
     * @param {HTMLSelectElement} select - The select element
     * @param {string} value - Option value
     * @param {string} label - Option display text
     */
    function appendOption(select, value, label) {
        const o = document.createElement('option');
        o.value = value;
        o.textContent = label;
        select.appendChild(o);
    }

    /**
     * Populate status options based on current project status
     * @param {string} currentStatus - The current status of the project
     */
    function populateStatusOptions(currentStatus) {
        // Use STATUS_LABELS mapping rendered server-side (guaranteed)
        newStatusSelect.innerHTML = '<option value="">Select status</option>';

        if (currentStatus === 'Ongoing') {
            // only allow Submitted
            if (STATUS_LABELS['Submitted']) appendOption(newStatusSelect, 'Submitted', STATUS_LABELS['Submitted']);
            statusSelectRow.style.display = '';
        } else if (currentStatus === 'Submitted') {
            // allow Won or Lost
            ['Won', 'Lost'].forEach(k => {
                if (STATUS_LABELS[k]) appendOption(newStatusSelect, k, STATUS_LABELS[k]);
            });
            statusSelectRow.style.display = '';
        } else {
            // Won / Lost - no transitions via this select
            statusSelectRow.style.display = 'none';
        }
    }

    // react to status select change to show/hide context fields
    newStatusSelect.addEventListener('change', function () {
        const sel = this.value;
        // submission date only for Submitted
        submissionRow.style.display = sel === 'Submitted' ? '' : 'none';
        // award date only when selecting Won
        awardRow.style.display = sel === 'Won' ? '' : 'none';
        // contract date only when project is already Won (modalCurrentStatus === 'Won')
        contractDateRow.style.display = (modalCurrentStatus === 'Won') ? '' : 'none';
        // lost date + competitor when selecting Lost
        lostRow.style.display = sel === 'Lost' ? '' : 'none';
        competitorField.style.display = sel === 'Lost' ? '' : 'none';
        // contractRows are shown only when project is already Won (modalCurrentStatus === 'Won')
        contractRows.style.display = (modalCurrentStatus === 'Won') ? '' : 'none';
    });

    // Open modal when status-pill clicked
    document.querySelectorAll('.status-pill').forEach(btn => {
        btn.addEventListener('click', function () {
            resetModalVisibility();

            const projectId = this.dataset.projectId;
            const currentStatus = this.dataset.status;
            modalCurrentStatus = currentStatus; // remember current status for this modal session

            const submission = this.dataset.submission || '';
            const award = this.dataset.award || '';
            const contract = this.dataset.contract || '';
            const lost = this.dataset.lost || '';
            const start = this.dataset.start || '';
            const end = this.dataset.end || '';

            // populate contextual date fields
            submissionDate.value = submission;
            awardDate.value = award;
            contractDate.value = contract;
            lostDate.value = lost;

            // populate contract actual dates (only displayed for current Won)
            actualStartInput.value = start;
            actualEndInput.value = end;

            // prepare select options based on current status
            populateStatusOptions(currentStatus);

            // show relevant rows for current status
            if (currentStatus === 'Won') {
                // show award + contract rows; allow editing award/contract
                awardRow.style.display = '';
                contractDateRow.style.display = '';
                contractRows.style.display = '';
                statusSelectRow.style.display = 'none';
            } else if (currentStatus === 'Lost') {
                // show lost + competitor; do NOT show contract rows for Lost
                lostRow.style.display = '';
                competitorField.style.display = '';
                statusSelectRow.style.display = 'none';
            } else {
                // Ongoing or Submitted: let populateStatusOptions control the select visibility
            }

            // update form action and project id hidden
            const newAction = defaultAction.replace('/0/', '/' + projectId + '/');
            contractForm.setAttribute('action', newAction);
            document.getElementById('contractProjectId').value = projectId;

            computeAndShowDuration();
            modal.show();
        });
    });

    contractModalEl.addEventListener('shown.bs.modal', computeAndShowDuration);
}

// Initialize status modal when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    initStatusModal();
});

// Expose chart init functions to global scope in case templates call them directly
// (some environments or bundlers may not automatically attach top-level functions to window)
if (typeof window !== 'undefined') {
    window.initWinLostChart = initWinLostChart;
    window.initEbitDayChart = initEbitDayChart;
    window.initStatusModal = initStatusModal;
}
