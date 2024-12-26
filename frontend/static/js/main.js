// API Configuration
const API_ENDPOINTS = {
    servers: '/api/servers',
    analyze: '/api/analyze',
    estimate: '/api/estimate',
    roadmap: '/api/roadmap',
    upload: '/api/upload-test-data',
    freeTier: '/api/free-tier-usage',
    config: '/api/check-config'
};

// State Management
let state = {
    servers: [],
    selectedServer: null,
    config: {
        region: 'us-east-1',
        mode: 'test'
    },
    progress: {
        analyzed: 0,
        total: 0,
        migration: 0
    }
};

// DOM Elements
const elements = {
    serverList: document.getElementById('serverList'),
    analysisResults: document.getElementById('analysisResults'),
    costEstimate: document.getElementById('costEstimate'),
    roadmapTimeline: document.getElementById('roadmapTimeline'),
    loadingIndicator: document.getElementById('loadingIndicator'),
    uploadForm: document.getElementById('uploadForm'),
    configStatus: document.getElementById('configStatus'),
    uploadModal: document.getElementById('uploadModal'),
    refreshBtn: document.getElementById('refreshServers'),
    generateRoadmapBtn: document.getElementById('generateRoadmap'),
    uploadTestDataBtn: document.getElementById('uploadTestData'),
    modalCloseBtn: document.querySelector('.modal-close'),
    awsRegion: document.getElementById('awsRegion')
};

// Initialize Application
async function initializeApp() {
    try {
        await checkConfiguration();
        await loadServers();
        updateFreeTierUsage();
        setupEventListeners();
        startAutoRefresh();
    } catch (error) {
        showError('Failed to initialize application: ' + error.message);
    }
}

// Configuration Check
async function checkConfiguration() {
    try {
        const response = await fetch(API_ENDPOINTS.config);
        const config = await response.json();
        
        state.config = {
            ...state.config,
            ...config
        };

        updateConfigurationStatus();
    } catch (error) {
        showError('Configuration check failed: ' + error.message);
    }
}

function updateConfigurationStatus() {
    const statusHtml = `
        <div class="config-status ${state.config.configured ? 'success' : 'warning'}">
            <i class="fas fa-${state.config.configured ? 'check-circle' : 'exclamation-triangle'}"></i>
            <span>Mode: ${state.config.mode.toUpperCase()}</span>
        </div>
    `;
    elements.configStatus.innerHTML = statusHtml;
}

// Server Management
async function loadServers() {
    showLoading(true);
    try {
        const response = await fetch(API_ENDPOINTS.servers);
        const data = await response.json();
        
        state.servers = data.servers || [];
        state.progress.total = state.servers.length;
        
        renderServerList();
        updateProgress();
    } catch (error) {
        showError('Failed to load servers: ' + error.message);
    } finally {
        showLoading(false);
    }
}

function renderServerList() {
    if (!state.servers.length) {
        elements.serverList.innerHTML = `
            <div class="placeholder-text">
                <i class="fas fa-server"></i>
                <p>No servers discovered yet</p>
            </div>
        `;
        return;
    }

    elements.serverList.innerHTML = state.servers.map(server => `
        <div class="server-item ${state.selectedServer?.serverId === server.serverId ? 'selected' : ''}"
             data-server-id="${server.serverId}">
            <div class="server-header">
                <h3>
                    <i class="fas fa-${getServerTypeIcon(server.serverType)}"></i>
                    ${server.serverName}
                </h3>
                <span class="status status-${getServerStatus(server)}">
                    <i class="fas fa-circle"></i>
                    ${getServerStatusText(server)}
                </span>
            </div>
            <div class="server-details">
                <p><i class="fas fa-microchip"></i> CPU: ${server.metrics.cpu.cores} cores (${server.metrics.cpu.utilization}% used)</p>
                <p><i class="fas fa-memory"></i> Memory: ${formatBytes(server.metrics.memory.total)} (${calculateUsagePercentage(server.metrics.memory)}% used)</p>
                <p><i class="fas fa-hdd"></i> Storage: ${formatBytes(server.metrics.storage.total)} (${calculateUsagePercentage(server.metrics.storage)}% used)</p>
                <p><i class="fas fa-network-wired"></i> Network: ${formatNetworkSpeed(server.metrics.network?.bandwidth || 0)}</p>
            </div>
            ${server.analysisStatus ? `
                <div class="analysis-status">
                    <i class="fas fa-info-circle"></i>
                    ${server.analysisStatus}
                </div>
            ` : ''}
        </div>
    `).join('');
}

// Server Analysis
async function analyzeServer(serverId) {
    showLoading(true);
    try {
        const response = await fetch(API_ENDPOINTS.analyze, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serverId })
        });
        
        const analysis = await response.json();
        renderAnalysisResults(analysis);
        state.progress.analyzed++;
        updateProgress();
        
        // Trigger cost estimation after analysis
        await estimateCosts(serverId);
    } catch (error) {
        showError('Analysis failed: ' + error.message);
    } finally {
        showLoading(false);
    }
}

function renderAnalysisResults(analysis) {
    if (!analysis || !analysis.results || !analysis.results[0]) {
        showError('Invalid analysis data received');
        return;
    }

    const result = analysis.results[0];
    elements.analysisResults.innerHTML = `
        <div class="analysis-content">
            <div class="analysis-section">
                <h3><i class="fas fa-chart-line"></i> Complexity Analysis</h3>
                <div class="complexity-score">
                    <div class="score-value ${getComplexityClass(result.complexity.level)}">
                        ${result.complexity.score}/10
                    </div>
                    <div class="score-label">
                        ${result.complexity.level} Complexity
                    </div>
                </div>
                <p>${result.complexity.description}</p>
            </div>

            <div class="analysis-section">
                <h3><i class="fas fa-route"></i> Migration Strategy</h3>
                <div class="strategy-details">
                    <div class="strategy-type">
                        <i class="fas fa-${getMigrationStrategyIcon(result.migrationStrategy.strategy)}"></i>
                        ${result.migrationStrategy.strategy}
                    </div>
                    <div class="risk-level ${result.migrationStrategy.risk_level.toLowerCase()}">
                        <i class="fas fa-exclamation-triangle"></i>
                        ${result.migrationStrategy.risk_level} Risk
                    </div>
                </div>
                <p>${result.migrationStrategy.description}</p>
            </div>

            <div class="analysis-section">
                <h3><i class="fas fa-project-diagram"></i> Dependencies</h3>
                ${result.dependencies.map(dep => `
                    <div class="dependency-item">
                        <div class="dependency-icon">
                            <i class="fas fa-${getDependencyIcon(dep.type)}"></i>
                        </div>
                        <div class="dependency-details">
                            <span class="dependency-name">${dep.name}</span>
                            <span class="dependency-type">${dep.type}</span>
                            <p class="dependency-desc">${dep.description}</p>
                        </div>
                    </div>
                `).join('')}
            </div>
        </div>
    `;
}

// Cost Estimation
async function estimateCosts(serverId) {
    try {
        const response = await fetch(API_ENDPOINTS.estimate, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serverId })
        });
        
        const estimate = await response.json();
        renderCostEstimate(estimate);
    } catch (error) {
        showError('Cost estimation failed: ' + error.message);
    }
}

function renderCostEstimate(estimate) {
    if (!estimate || !estimate.summary) {
        elements.costEstimate.innerHTML = `
            <div class="placeholder-text">
                <i class="fas fa-calculator"></i>
                <p>Select a server to view cost estimates</p>
            </div>`;
        return;
    }

    elements.costEstimate.innerHTML = `
        <div class="cost-content">
            <div class="cost-section">
                <h3><i class="fas fa-coins"></i> Monthly Cost Analysis</h3>
                <div class="cost-grid">
                    <div class="cost-item">
                        <span class="label">Current Cost</span>
                        <span class="value">₹${formatCurrency(estimate.summary.currentMonthlyCost)}</span>
                    </div>
                    <div class="cost-item">
                        <span class="label">Projected Cost</span>
                        <span class="value">₹${formatCurrency(estimate.summary.projectedMonthlyCost)}</span>
                    </div>
                    <div class="cost-item savings">
                        <span class="label">Monthly Savings</span>
                        <span class="value">₹${formatCurrency(estimate.summary.monthlySavings)}</span>
                    </div>
                </div>
            </div>

            <div class="cost-section">
                <h3><i class="fas fa-chart-line"></i> ROI Analysis</h3>
                <div class="cost-grid">
                    <div class="cost-item">
                        <span class="label">Migration Cost</span>
                        <span class="value">₹${formatCurrency(estimate.summary.migrationCost)}</span>
                    </div>
                    <div class="cost-item">
                        <span class="label">Break-even Period</span>
                        <span class="value">${Number(estimate.summary.roiMonths).toFixed(1)} months</span>
                    </div>
                    <div class="cost-item">
                        <span class="label">3-Year Savings</span>
                        <span class="value">₹${formatCurrency(estimate.summary.threeYearSavings)}</span>
                    </div>
                </div>
            </div>

            ${estimate.optimization ? `
                <div class="cost-section">
                    <h3><i class="fas fa-lightbulb"></i> Optimization Recommendations</h3>
                    <div class="recommendations-list">
                        ${estimate.optimization.recommendations.map(rec => `
                            <div class="recommendation-item">
                                <i class="fas fa-check-circle"></i>
                                <span>${rec}</span>
                            </div>
                        `).join('')}
                    </div>
                    <div class="potential-savings">
                        <h4>Potential Additional Savings:</h4>
                        <ul>
                            ${Object.entries(estimate.optimization.potentialSavings).map(([key, value]) => `
                                <li>${key.replace(/([A-Z])/g, ' $1').trim()}: ${value}</li>
                            `).join('')}
                        </ul>
                    </div>
                </div>
            ` : ''}
        </div>
    `;
}

// Roadmap Generation
async function generateRoadmap() {
    showLoading(true);
    try {
        const response = await fetch(API_ENDPOINTS.roadmap, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ servers: state.servers })
        });
        
        const roadmap = await response.json();
        renderRoadmap(roadmap);
    } catch (error) {
        showError('Failed to generate roadmap: ' + error.message);
    } finally {
        showLoading(false);
    }
}

function renderRoadmap(roadmap) {
    const roadmapContainer = document.getElementById('roadmapTimeline');
    const timelineContent = roadmap.timeline.map((phase, index) => `
        <div class="phase ${phase.name.toLowerCase().replace(' ', '-')}">
            <div class="phase-header">
                <div class="phase-number">${index + 1}</div>
                <div class="phase-info">
                    <h3>${phase.name}</h3>
                    <span class="phase-duration">${phase.duration}</span>
                </div>
            </div>
            <div class="phase-dates">
                <i class="far fa-calendar-alt"></i>
                ${phase.startDate} - ${phase.endDate}
            </div>
            <div class="phase-content">
                <div class="phase-tasks">
                    ${phase.tasks.map(task => `
                        <div class="task-item">
                            <i class="fas fa-check-circle"></i>
                            <span>${task}</span>
                        </div>
                    `).join('')}
                </div>
                ${phase.risks ? `
                    <div class="phase-risks">
                        <h4><i class="fas fa-exclamation-triangle"></i> Risks</h4>
                        ${phase.risks.map(risk => `
                            <div class="risk-item ${risk.severity.toLowerCase()}">
                                <span class="risk-indicator"></span>
                                <span class="risk-description">${risk.description}</span>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
                <div class="phase-milestones">
                    <h4><i class="fas fa-flag-checkered"></i> Milestones</h4>
                    <ul>
                        ${phase.milestones.map(milestone => `
                            <li>
                                <i class="fas fa-star"></i>
                                <span>${milestone}</span>
                            </li>
                        `).join('')}
                    </ul>
                </div>
            </div>
        </div>
    `).join('');

    const projectSummary = `
        <div class="summary-stats">
            <div class="stat-item">
                <h4>Total Duration</h4>
                <p>${roadmap.projectSummary.duration}</p>
            </div>
            <div class="stat-item">
                <h4>Total Servers</h4>
                <p>${roadmap.projectSummary.totalServers}</p>
            </div>
            <div class="stat-item">
                <h4>Total Effort</h4>
                <p>${roadmap.projectSummary.totalEffort} hours</p>
            </div>
        </div>
        <div class="critical-path">
            <h4><i class="fas fa-route"></i> Critical Path</h4>
            <div class="path-visualization">
                ${roadmap.projectSummary.criticalPath.map((server, index, array) => `
                    <span class="path-node">${server}</span>
                    ${index < array.length - 1 ? '<i class="fas fa-arrow-right"></i>' : ''}
                `).join('')}
            </div>
        </div>
    `;

    roadmapContainer.innerHTML = `
        <div class="timeline">
            ${timelineContent}
        </div>
        <div class="project-summary">
            ${projectSummary}
        </div>
    `;

    // Update progress
    function updateMigrationProgress(timeline) {
        const totalPhases = timeline.length;
        const completedPhases = timeline.filter(phase => 
            new Date(phase.endDate) < new Date()
        ).length;
        
        const progress = (completedPhases / totalPhases) * 100;
        
        document.getElementById('migrationProgress').textContent = `${Math.round(progress)}%`;
        updateProgressBar('migrationProgressBar', progress);
        
        // Update individual phase progress
        timeline.forEach((phase, index) => {
            const phaseProgress = calculatePhaseProgress(phase);
            document.getElementById(`phase${index + 1}Progress`).style.width = `${phaseProgress}%`;
        });
    }
    
    function calculatePhaseProgress(phase) {
        const start = new Date(phase.startDate);
        const end = new Date(phase.endDate);
        const now = new Date();
        
        if (now < start) return 0;
        if (now > end) return 100;
        
        const total = end - start;
        const current = now - start;
        return Math.round((current / total) * 100);
    }
}

// Free Tier Usage Monitoring
async function updateFreeTierUsage() {
    try {
        const response = await fetch(API_ENDPOINTS.freeTier);
        const usage = await response.json();
        
        updateUsageMetrics(usage);
        updateUsageProgressBars(usage);
    } catch (error) {
        console.error('Failed to update Free Tier usage:', error);
    }
}

function updateUsageMetrics(usage) {
    // Update Lambda usage
    document.getElementById('lambdaUsage').textContent = 
        `${formatNumber(usage.lambda.used)}/${formatNumber(usage.lambda.limit)} invocations`;
    
    // Update S3 usage
    document.getElementById('s3Usage').textContent = 
        `${usage.s3.used.toFixed(2)}/${usage.s3.limit} GB`;
    
    // Update DynamoDB usage
    document.getElementById('dynamodbUsage').textContent = 
        `${usage.dynamodb.used.toFixed(2)}/${usage.dynamodb.limit} GB`;
    
    // Update API Gateway usage
    document.getElementById('apiGatewayUsage').textContent = 
        `${formatNumber(usage.apiGateway.used)}/${formatNumber(usage.apiGateway.limit)} requests`;
}

function updateUsageProgressBars(usage) {
    // Calculate and update progress bar percentages
    updateProgressBar('lambdaProgress', (usage.lambda.used / usage.lambda.limit) * 100);
    updateProgressBar('s3Progress', (usage.s3.used / usage.s3.limit) * 100);
    updateProgressBar('dynamodbProgress', (usage.dynamodb.used / usage.dynamodb.limit) * 100);
    updateProgressBar('apiGatewayProgress', (usage.apiGateway.used / usage.apiGateway.limit) * 100);
}

// Progress Tracking
function updateProgress() {
    const analyzedServers = document.getElementById('serversAnalyzed');
    const migrationProgress = document.getElementById('migrationProgress');
    
    // Update servers analyzed
    analyzedServers.textContent = `${state.progress.analyzed}/${state.progress.total}`;
    updateProgressBar('analysisProgress', (state.progress.analyzed / state.progress.total) * 100);
    
    // Update migration progress
    migrationProgress.textContent = `${state.progress.migration}%`;
    updateProgressBar('migrationProgressBar', state.progress.migration);
}

// Utility Functions
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatNetworkSpeed(bps) {
    if (bps === 0) return '0 Mbps';
    
    const mbps = bps / 1000000;
    return `${mbps.toFixed(2)} Mbps`;
}

function formatNumber(num) {
    return new Intl.NumberFormat().format(num);
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-IN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(amount);
}

function calculateUsagePercentage(metric) {
    return ((metric.used / metric.total) * 100).toFixed(1);
}

function updateProgressBar(elementId, percentage) {
    const progressBar = document.getElementById(elementId);
    if (progressBar) {
        const clampedPercentage = Math.min(Math.max(percentage, 0), 100);
        progressBar.style.width = `${clampedPercentage}%`;
        
        // Update color based on usage
        if (clampedPercentage > 80) {
            progressBar.classList.add('critical');
        } else if (clampedPercentage > 60) {
            progressBar.classList.add('warning');
        }
    }
}

function getServerStatus(server) {
    const cpuUtilization = server.metrics.cpu.utilization;
    if (cpuUtilization > 80) return 'danger';
    if (cpuUtilization > 60) return 'warning';
    return 'success';
}

function getServerStatusText(server) {
    const status = getServerStatus(server);
    switch (status) {
        case 'danger': return 'High Load';
        case 'warning': return 'Moderate Load';
        case 'success': return 'Normal';
        default: return 'Unknown';
    }
}

function getServerTypeIcon(serverType) {
    switch (serverType?.toLowerCase()) {
        case 'linux': return 'linux';
        case 'windows': return 'windows';
        case 'macos': return 'apple';
        default: return 'server';
    }
}

function getDependencyIcon(dependencyType) {
    switch (dependencyType?.toLowerCase()) {
        case 'database': return 'database';
        case 'cache': return 'memory';
        case 'messaging': return 'envelope';
        case 'storage': return 'hdd';
        default: return 'layer-group';
    }
}

function getMigrationStrategyIcon(strategy) {
    switch (strategy?.toLowerCase()) {
        case 'rehost': return 'truck-moving';
        case 'replatform': return 'layer-group';
        case 'refactor': return 'code';
        default: return 'random';
    }
}

function getComplexityClass(level) {
    switch (level?.toLowerCase()) {
        case 'high': return 'complexity-high';
        case 'medium': return 'complexity-medium';
        case 'low': return 'complexity-low';
        default: return 'complexity-unknown';
    }
}

// Error Handling
function showError(message) {
    const errorDiv = document.createElement('div');
    errorDiv.className = 'alert alert-danger';
    errorDiv.innerHTML = `
        <i class="fas fa-exclamation-circle"></i>
        <span>${message}</span>
    `;
    document.body.appendChild(errorDiv);
    
    setTimeout(() => {
        errorDiv.remove();
    }, 5000);
}

function showSuccess(message) {
    const successDiv = document.createElement('div');
    successDiv.className = 'alert alert-success';
    successDiv.innerHTML = `
        <i class="fas fa-check-circle"></i>
        <span>${message}</span>
    `;
    document.body.appendChild(successDiv);
    
    setTimeout(() => {
        successDiv.remove();
    }, 3000);
}

// Loading State Management
function showLoading(show) {
    if (show) {
        elements.loadingIndicator.style.display = 'flex';
        document.body.classList.add('loading');
    } else {
        elements.loadingIndicator.style.display = 'none';
        document.body.classList.remove('loading');
    }
}

// Event Listeners
function setupEventListeners() {
    // Server list click handler
    elements.serverList.addEventListener('click', (e) => {
        const serverItem = e.target.closest('.server-item');
        if (serverItem) {
            const serverId = serverItem.dataset.serverId;
            state.selectedServer = state.servers.find(s => s.serverId === serverId);
            analyzeServer(serverId);
        }
    });

    // Refresh button
    elements.refreshBtn.addEventListener('click', loadServers);

    // Generate roadmap button
    elements.generateRoadmapBtn.addEventListener('click', generateRoadmap);

    // Upload test data button
    elements.uploadTestDataBtn.addEventListener('click', () => {
        elements.uploadModal.style.display = 'flex';
    });

    // Modal close button
    elements.modalCloseBtn.addEventListener('click', () => {
        elements.uploadModal.style.display = 'none';
    });

    // Upload form submission
    elements.uploadForm.addEventListener('submit', handleTestDataUpload);

    // Region selection
    elements.awsRegion.addEventListener('change', (e) => {
        state.config.region = e.target.value;
        loadServers();
    });
}

// Handle test data upload
// Update the handleTestDataUpload function
async function handleTestDataUpload(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    
    try {
        showLoading(true);
        const response = await fetch(API_ENDPOINTS.upload, {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showSuccess('Test data uploaded successfully');
            state.servers = result.servers;
            renderServerList();
            elements.uploadModal.style.display = 'none';
            e.target.reset(); // Reset form
        } else {
            throw new Error(result.error || 'Upload failed');
        }
    } catch (error) {
        showError('Failed to upload test data: ' + error.message);
    } finally {
        showLoading(false);
    }
}

// Auto-refresh mechanism
function startAutoRefresh() {
    // Update Free Tier usage every minute
    setInterval(updateFreeTierUsage, 60000);
    
    // Refresh server list every 5 minutes
    setInterval(loadServers, 300000);
}

// Initialize the application
document.addEventListener('DOMContentLoaded', initializeApp);
