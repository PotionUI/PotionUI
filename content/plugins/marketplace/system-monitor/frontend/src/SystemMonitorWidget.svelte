<script>
    let { context = {} } = $props();

    let stats = $state(null);
    let connected = $state(false);
    let activeTooltip = $state(null);
    let tooltipElement = $state(null);
    let tooltipReady = $state(false);
    let tooltipStyle = $state('');
    // Plain variables - NOT $state to avoid reactivity loops inside $effect
    let ws = null;
    let reconnectTimeout = null;
    let tooltipAnchor = null;
    let tooltipTimeout = null;

    function connectWebSocket() {
        try {
            // Don't connect if already connected
            if (ws && ws.readyState === WebSocket.OPEN) {
                return;
            }

            // Clean up any existing connection first
            if (ws && ws.readyState !== WebSocket.CLOSED) {
                try {
                    ws.close();
                } catch (e) {
                    console.warn('[SystemMonitor] Error closing existing WebSocket:', e);
                }
            }

            const token = context.token;
            if (!token || !context.wsUrl) {
                console.warn('[SystemMonitor] No token or wsUrl available');
                return;
            }

            const wsInstance = new WebSocket(context.wsUrl('/ws/system', token));

            wsInstance.onopen = () => {
                console.debug('[SystemMonitor] WebSocket connected');
                connected = true;
                ws = wsInstance;
            };

            wsInstance.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    if (message.type === 'system_update' && message.data) {
                        stats = message.data;
                        connected = true;
                    } else if (message.type === 'ping') {
                        // Respond to server ping with pong
                        try {
                            wsInstance.send(JSON.stringify({ type: 'pong' }));
                        } catch (e) {
                            console.warn('[SystemMonitor] Failed to send pong:', e);
                        }
                    }
                } catch (err) {
                    console.error('[SystemMonitor] Error parsing message:', err);
                }
            };

            wsInstance.onclose = (event) => {
                console.debug('[SystemMonitor] WebSocket closed:', event.code, event.reason);
                connected = false;
                ws = null;

                // Only reconnect for unexpected disconnections and only if not already scheduled
                if (event.code !== 1000 && event.code !== 1001 && !reconnectTimeout) {
                    const token = context.token;
                    if (token) {
                        reconnectTimeout = setTimeout(() => {
                            reconnectTimeout = null;
                            connectWebSocket();
                        }, 30000); // 30 seconds
                    }
                }
            };

            wsInstance.onerror = (errorEvent) => {
                console.error('[SystemMonitor] WebSocket error:', errorEvent);
                connected = false;
            };

            ws = wsInstance;
        } catch (err) {
            console.error('[SystemMonitor] Failed to connect:', err);
        }
    }

    function formatBytes(bytes) {
        if (bytes === 0) return '0 MB';
        const mb = bytes;
        if (mb < 1024) return `${mb.toFixed(0)} MB`;
        const gb = mb / 1024;
        return `${gb.toFixed(1)} GB`;
    }

    function getUsageStroke(percent) {
        if (percent < 50) return '#34d399'; // emerald-400
        if (percent < 80) return '#fbbf24'; // amber-400
        return '#f87171'; // red-400
    }

    function calculateCircleOffset(percent, radius) {
        const circumference = 2 * Math.PI * radius;
        return circumference * (1 - percent / 100);
    }

    function portal(node) {
        document.body.appendChild(node);

        return {
            destroy() {
                node.remove();
            }
        };
    }

    function positionTooltip() {
        if (!tooltipAnchor || !tooltipElement || !activeTooltip) return;

        const anchorRect = tooltipAnchor.getBoundingClientRect();
        const tooltipRect = tooltipElement.getBoundingClientRect();
        const viewportPadding = 12;
        const gap = 12;

        let left = anchorRect.right + gap;
        let top = anchorRect.top + anchorRect.height / 2 - tooltipRect.height / 2;

        if (left + tooltipRect.width > window.innerWidth - viewportPadding) {
            left = anchorRect.left - tooltipRect.width - gap;
        }

        left = Math.max(
            viewportPadding,
            Math.min(left, window.innerWidth - tooltipRect.width - viewportPadding)
        );
        top = Math.max(
            viewportPadding,
            Math.min(top, window.innerHeight - tooltipRect.height - viewportPadding)
        );

        tooltipStyle = `left: ${Math.round(left)}px; top: ${Math.round(top)}px;`;
        tooltipReady = true;
    }

    function showTooltip(kind, event, delay = 90) {
        if (tooltipTimeout) clearTimeout(tooltipTimeout);
        tooltipAnchor = event.currentTarget;

        tooltipTimeout = setTimeout(() => {
            activeTooltip = kind;
            tooltipReady = false;
            requestAnimationFrame(() => requestAnimationFrame(positionTooltip));
        }, delay);
    }

    function hideTooltip() {
        if (tooltipTimeout) {
            clearTimeout(tooltipTimeout);
            tooltipTimeout = null;
        }
        activeTooltip = null;
        tooltipAnchor = null;
        tooltipReady = false;
    }

    function handleTooltipKeydown(event) {
        if (event.key === 'Escape') hideTooltip();
    }

    function getUsageLabel(percent) {
        if (percent < 50) return 'Plenty of headroom';
        if (percent < 80) return 'Moderate load';
        return 'Running near capacity';
    }

    function getTooltipAccent() {
        if (activeTooltip === 'connection') return connected ? '#34d399' : '#f87171';
        if (activeTooltip === 'gpu') return getUsageStroke(stats?.gpu?.vram_usage_percent ?? 0);
        if (activeTooltip === 'ram') return getUsageStroke(stats?.ram?.usage_percent ?? 0);
        if (activeTooltip === 'cpu') return getUsageStroke(stats?.cpu?.usage_percent ?? 0);
        return '#60a5fa';
    }

    $effect(() => {
        if (context.token && context.wsUrl) {
            connectWebSocket();
        }
        return () => {
            if (reconnectTimeout) clearTimeout(reconnectTimeout);
            if (tooltipTimeout) clearTimeout(tooltipTimeout);
            if (ws) ws.close(1000, 'Widget unmounting');
        };
    });
</script>

<svelte:window onresize={positionTooltip} onkeydown={handleTooltipKeydown} />

<!-- Ultra-compact mode for sidebar. Tooltips are portalled below so they do not
     inherit the sidebar's stacking context. -->
<div class="resource-monitor" aria-label="System resources">
    <button
        type="button"
        class="connection-trigger"
        aria-label={connected ? 'Resource monitor connected' : 'Resource monitor disconnected'}
        aria-describedby={activeTooltip === 'connection' ? 'resource-monitor-tooltip' : undefined}
        onmouseenter={(event) => showTooltip('connection', event)}
        onmouseleave={hideTooltip}
        onfocus={(event) => showTooltip('connection', event, 0)}
        onblur={hideTooltip}
    >
        <span class:connected class="connection-dot"></span>
    </button>

    {#if stats}
        {#if stats.gpu.available}
            <button
                type="button"
                class="resource-trigger"
                aria-label={`GPU memory ${stats.gpu.vram_usage_percent.toFixed(0)} percent used`}
                aria-describedby={activeTooltip === 'gpu' ? 'resource-monitor-tooltip' : undefined}
                onmouseenter={(event) => showTooltip('gpu', event)}
                onmouseleave={hideTooltip}
                onfocus={(event) => showTooltip('gpu', event, 0)}
                onblur={hideTooltip}
            >
                <svg class="usage-ring" viewBox="0 0 32 32" aria-hidden="true">
                    <circle class="usage-ring-track" cx="16" cy="16" r="14" fill="none" stroke-width="2" />
                    <circle
                        cx="16"
                        cy="16"
                        r="14"
                        fill="none"
                        stroke={getUsageStroke(stats.gpu.vram_usage_percent)}
                        stroke-width="2"
                        stroke-dasharray={2 * Math.PI * 14}
                        stroke-dashoffset={calculateCircleOffset(stats.gpu.vram_usage_percent, 14)}
                        class="usage-ring-value"
                        stroke-linecap="round"
                    />
                </svg>
                <svg class="trigger-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
            </button>
        {/if}

        {#if stats.ram.available}
            <button
                type="button"
                class="resource-trigger"
                aria-label={`System memory ${stats.ram.usage_percent.toFixed(0)} percent used`}
                aria-describedby={activeTooltip === 'ram' ? 'resource-monitor-tooltip' : undefined}
                onmouseenter={(event) => showTooltip('ram', event)}
                onmouseleave={hideTooltip}
                onfocus={(event) => showTooltip('ram', event, 0)}
                onblur={hideTooltip}
            >
                <svg class="usage-ring" viewBox="0 0 32 32" aria-hidden="true">
                    <circle class="usage-ring-track" cx="16" cy="16" r="14" fill="none" stroke-width="2" />
                    <circle
                        cx="16"
                        cy="16"
                        r="14"
                        fill="none"
                        stroke={getUsageStroke(stats.ram.usage_percent)}
                        stroke-width="2"
                        stroke-dasharray={2 * Math.PI * 14}
                        stroke-dashoffset={calculateCircleOffset(stats.ram.usage_percent, 14)}
                        class="usage-ring-value"
                        stroke-linecap="round"
                    />
                </svg>
                <svg class="trigger-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375" />
                </svg>
            </button>
        {/if}

        {#if stats.cpu.available}
            <button
                type="button"
                class="resource-trigger"
                aria-label={`CPU ${stats.cpu.usage_percent.toFixed(0)} percent used`}
                aria-describedby={activeTooltip === 'cpu' ? 'resource-monitor-tooltip' : undefined}
                onmouseenter={(event) => showTooltip('cpu', event)}
                onmouseleave={hideTooltip}
                onfocus={(event) => showTooltip('cpu', event, 0)}
                onblur={hideTooltip}
            >
                <svg class="usage-ring" viewBox="0 0 32 32" aria-hidden="true">
                    <circle class="usage-ring-track" cx="16" cy="16" r="14" fill="none" stroke-width="2" />
                    <circle
                        cx="16"
                        cy="16"
                        r="14"
                        fill="none"
                        stroke={getUsageStroke(stats.cpu.usage_percent)}
                        stroke-width="2"
                        stroke-dasharray={2 * Math.PI * 14}
                        stroke-dashoffset={calculateCircleOffset(stats.cpu.usage_percent, 14)}
                        class="usage-ring-value"
                        stroke-linecap="round"
                    />
                </svg>
                <svg class="trigger-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h10.5a2.25 2.25 0 002.25-2.25V6.75a2.25 2.25 0 00-2.25-2.25H6.75A2.25 2.25 0 004.5 6.75v10.5a2.25 2.25 0 002.25 2.25zm.75-12h9v9h-9v-9z" />
                </svg>
            </button>
        {/if}
    {/if}
</div>

{#if activeTooltip}
    <div
        use:portal
        bind:this={tooltipElement}
        id="resource-monitor-tooltip"
        role="tooltip"
        class:tooltip-ready={tooltipReady}
        class="resource-tooltip"
        style={`${tooltipStyle} --resource-accent: ${getTooltipAccent()};`}
    >
        {#if activeTooltip === 'connection'}
            <div class="tooltip-header connection-header">
                <div class="tooltip-icon connection-icon">
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.807 9.981-3.807 13.788 0M1.924 8.674c5.564-5.564 14.588-5.564 20.152 0M12 18.75h.008v.008H12v-.008z" />
                    </svg>
                </div>
                <div class="tooltip-heading">
                    <div class="tooltip-title">Resource monitor</div>
                    <div class="tooltip-subtitle">Live hardware telemetry</div>
                </div>
                <span class:online={connected} class="connection-badge">
                    <span></span>{connected ? 'Live' : 'Offline'}
                </span>
            </div>
            <p class="connection-copy">
                {connected
                    ? 'GPU, memory, and processor usage are updating in real time.'
                    : 'Waiting for a connection to the system telemetry service.'}
            </p>
        {:else if stats}
            {@const usage = activeTooltip === 'gpu'
                ? stats.gpu.vram_usage_percent
                : activeTooltip === 'ram'
                    ? stats.ram.usage_percent
                    : stats.cpu.usage_percent}
            <div class="tooltip-header">
                <div class="tooltip-icon">
                    {#if activeTooltip === 'gpu'}
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
                    {:else if activeTooltip === 'ram'}
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375" />
                        </svg>
                    {:else}
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h10.5a2.25 2.25 0 002.25-2.25V6.75a2.25 2.25 0 00-2.25-2.25H6.75A2.25 2.25 0 004.5 6.75v10.5a2.25 2.25 0 002.25 2.25zm.75-12h9v9h-9v-9z" />
                        </svg>
                    {/if}
                </div>
                <div class="tooltip-heading">
                    <div class="tooltip-title">
                        {activeTooltip === 'gpu' ? 'GPU memory' : activeTooltip === 'ram' ? 'System memory' : 'CPU load'}
                    </div>
                    <div class="tooltip-subtitle">
                        {activeTooltip === 'gpu'
                            ? 'Graphics memory available for generation'
                            : activeTooltip === 'ram'
                                ? 'Memory shared by the app and system'
                                : `Activity across ${stats.cpu.core_count} logical cores`}
                    </div>
                </div>
                <div class="usage-value">{usage.toFixed(0)}<span>%</span></div>
            </div>

            <div class="progress-track" aria-hidden="true">
                <div class="progress-value" style={`width: ${Math.min(100, Math.max(0, usage))}%`}></div>
            </div>

            {#if activeTooltip === 'gpu'}
                <div class="metric-grid">
                    <div><span>Used</span><strong>{formatBytes(stats.gpu.vram_used)}</strong></div>
                    <div><span>Available</span><strong>{formatBytes(stats.gpu.vram_free)}</strong></div>
                    <div><span>Capacity</span><strong>{formatBytes(stats.gpu.vram_total)}</strong></div>
                    {#if typeof stats.gpu.temperature === 'number'}
                        <div><span>Temperature</span><strong>{stats.gpu.temperature}°C</strong></div>
                    {/if}
                </div>
            {:else if activeTooltip === 'ram'}
                <div class="metric-grid three-columns">
                    <div><span>Used</span><strong>{formatBytes(stats.ram.used)}</strong></div>
                    <div><span>Available</span><strong>{formatBytes(stats.ram.free)}</strong></div>
                    <div><span>Capacity</span><strong>{formatBytes(stats.ram.total)}</strong></div>
                </div>
            {:else}
                <div class="metric-grid two-columns">
                    <div><span>Current load</span><strong>{stats.cpu.usage_percent.toFixed(0)}%</strong></div>
                    <div><span>Logical cores</span><strong>{stats.cpu.core_count}</strong></div>
                </div>
            {/if}

            <div class="tooltip-status">
                <span class="status-mark">
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.25" d="M5 12.5l4 4L19 7" />
                    </svg>
                </span>
                {getUsageLabel(usage)}
            </div>
        {/if}
        <div class="tooltip-arrow"></div>
    </div>
{/if}

<style>
    .resource-monitor {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 6px;
    }

    .connection-trigger,
    .resource-trigger {
        appearance: none;
        border: 0;
        padding: 0;
        color: rgb(var(--fg-muted, 161 161 170));
        background: transparent;
        cursor: default;
    }

    .connection-trigger {
        display: grid;
        width: 24px;
        height: 14px;
        place-items: center;
        border-radius: 4px;
    }

    .connection-trigger:hover,
    .connection-trigger:focus-visible,
    .resource-trigger:hover,
    .resource-trigger:focus-visible {
        color: rgb(var(--fg, 244 244 245));
        background: rgb(var(--surface-2, 39 39 42));
        outline: none;
    }

    .connection-trigger:focus-visible,
    .resource-trigger:focus-visible {
        box-shadow: 0 0 0 2px rgb(var(--accent, 99 102 241) / 0.45);
    }

    .connection-dot {
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: #f87171;
        box-shadow: 0 0 0 3px rgb(248 113 113 / 0.1);
    }

    .connection-dot.connected {
        background: #34d399;
        box-shadow: 0 0 0 3px rgb(52 211 153 / 0.1), 0 0 8px rgb(52 211 153 / 0.3);
        animation: monitor-pulse 2.2s ease-in-out infinite;
    }

    .resource-trigger {
        position: relative;
        display: grid;
        width: 36px;
        height: 36px;
        place-items: center;
        border-radius: 7px;
        transition: color 120ms ease, background 120ms ease, transform 120ms ease;
    }

    .resource-trigger:hover {
        transform: translateX(1px);
    }

    .usage-ring {
        position: absolute;
        inset: 2px;
        width: 32px;
        height: 32px;
        transform: rotate(-90deg);
    }

    .usage-ring-track {
        stroke: rgb(var(--line-strong, 63 63 70));
    }

    .usage-ring-value {
        transition: stroke-dashoffset 300ms ease, stroke 200ms ease;
    }

    .trigger-icon {
        width: 14px;
        height: 14px;
    }

    .resource-tooltip {
        --resource-accent: #60a5fa;
        position: fixed;
        z-index: 2147483000;
        width: min(288px, calc(100vw - 24px));
        box-sizing: border-box;
        padding: 14px;
        color: rgb(var(--fg, 244 244 245));
        background: rgb(var(--surface-2, 30 30 33) / 0.98);
        border: 1px solid rgb(var(--line-strong, 63 63 70));
        border-radius: 10px;
        box-shadow: var(--shadow-overlay, 0 18px 48px rgb(0 0 0 / 0.45));
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
        pointer-events: none;
        opacity: 0;
        transform: translateX(-4px) scale(0.985);
        transform-origin: left center;
        transition: opacity 120ms ease, transform 120ms ease;
        isolation: isolate;
    }

    .resource-tooltip.tooltip-ready {
        opacity: 1;
        transform: translateX(0) scale(1);
    }

    .tooltip-header {
        display: flex;
        align-items: center;
        gap: 10px;
    }

    .tooltip-icon {
        display: grid;
        flex: 0 0 auto;
        width: 34px;
        height: 34px;
        place-items: center;
        color: var(--resource-accent);
        background: color-mix(in srgb, var(--resource-accent) 14%, transparent);
        border: 1px solid color-mix(in srgb, var(--resource-accent) 28%, transparent);
        border-radius: 7px;
    }

    .tooltip-icon svg {
        width: 17px;
        height: 17px;
    }

    .tooltip-heading {
        min-width: 0;
        flex: 1 1 auto;
    }

    .tooltip-title {
        font-size: 12px;
        line-height: 16px;
        font-weight: 650;
        letter-spacing: 0.01em;
        color: rgb(var(--fg, 244 244 245));
    }

    .tooltip-subtitle {
        overflow: hidden;
        margin-top: 1px;
        font-size: 10px;
        line-height: 14px;
        color: rgb(var(--fg-subtle, 113 113 122));
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .usage-value {
        flex: 0 0 auto;
        color: var(--resource-accent);
        font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 18px;
        line-height: 22px;
        font-weight: 700;
        letter-spacing: -0.04em;
    }

    .usage-value span {
        margin-left: 1px;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0;
    }

    .progress-track {
        height: 4px;
        margin: 12px 0;
        overflow: hidden;
        background: rgb(var(--surface-3, 39 39 42));
        border-radius: 999px;
    }

    .progress-value {
        height: 100%;
        min-width: 2px;
        background: var(--resource-accent);
        border-radius: inherit;
        box-shadow: 0 0 8px color-mix(in srgb, var(--resource-accent) 45%, transparent);
        transition: width 300ms ease, background 180ms ease;
    }

    .metric-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 1px;
        overflow: hidden;
        background: rgb(var(--line, 45 45 50));
        border: 1px solid rgb(var(--line, 45 45 50));
        border-radius: 7px;
    }

    .metric-grid.three-columns {
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }

    .metric-grid.two-columns {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .metric-grid > div {
        display: flex;
        min-width: 0;
        padding: 8px 9px;
        flex-direction: column;
        background: rgb(var(--surface-1, 24 24 27));
    }

    .metric-grid span {
        font-size: 9px;
        line-height: 12px;
        color: rgb(var(--fg-subtle, 113 113 122));
        text-transform: uppercase;
        letter-spacing: 0.055em;
    }

    .metric-grid strong {
        overflow: hidden;
        margin-top: 2px;
        color: rgb(var(--fg-muted, 161 161 170));
        font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 10px;
        line-height: 14px;
        font-weight: 600;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .tooltip-status {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 10px;
        color: rgb(var(--fg-muted, 161 161 170));
        font-size: 10px;
        line-height: 14px;
    }

    .status-mark {
        display: grid;
        width: 15px;
        height: 15px;
        place-items: center;
        color: var(--resource-accent);
        background: color-mix(in srgb, var(--resource-accent) 13%, transparent);
        border-radius: 999px;
    }

    .status-mark svg {
        width: 10px;
        height: 10px;
    }

    .connection-header {
        padding-right: 1px;
    }

    .connection-badge {
        display: inline-flex;
        flex: 0 0 auto;
        align-items: center;
        gap: 5px;
        padding: 3px 6px;
        color: #f87171;
        background: rgb(248 113 113 / 0.1);
        border: 1px solid rgb(248 113 113 / 0.2);
        border-radius: 999px;
        font-size: 9px;
        line-height: 12px;
        font-weight: 650;
        text-transform: uppercase;
        letter-spacing: 0.055em;
    }

    .connection-badge.online {
        color: #34d399;
        background: rgb(52 211 153 / 0.1);
        border-color: rgb(52 211 153 / 0.2);
    }

    .connection-badge > span {
        width: 5px;
        height: 5px;
        background: currentColor;
        border-radius: 999px;
    }

    .connection-copy {
        margin: 11px 0 0;
        padding-top: 10px;
        color: rgb(var(--fg-muted, 161 161 170));
        border-top: 1px solid rgb(var(--line, 45 45 50));
        font-size: 10px;
        line-height: 15px;
    }

    .tooltip-arrow {
        position: absolute;
        top: calc(50% - 5px);
        left: -5px;
        z-index: -1;
        width: 10px;
        height: 10px;
        background: rgb(var(--surface-2, 30 30 33));
        border-bottom: 1px solid rgb(var(--line-strong, 63 63 70));
        border-left: 1px solid rgb(var(--line-strong, 63 63 70));
        transform: rotate(45deg);
    }

    @keyframes monitor-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.58; }
    }

    @media (prefers-reduced-motion: reduce) {
        .connection-dot.connected,
        .resource-tooltip,
        .resource-trigger,
        .usage-ring-value,
        .progress-value {
            animation: none;
            transition: none;
        }
    }
</style>
