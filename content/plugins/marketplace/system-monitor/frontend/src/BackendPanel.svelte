<script>
    import HostUi from '../../../../sdk/HostUi.svelte';

    let { context = {}, onclose } = $props();

    const POLL_MS = 5000;

    const KIND_LABEL = {
        local: 'This machine',
        comfyui: 'ComfyUI server',
        worker: 'GPU worker',
        cloud: 'Cloud provider',
        other: 'Backend'
    };

    const STATUS = {
        online: { label: 'Online', variant: 'success' },
        degraded: { label: 'Degraded', variant: 'warning' },
        offline: { label: 'Offline', variant: 'danger' },
        unreachable: { label: 'Unreachable', variant: 'danger' },
        error: { label: 'Error', variant: 'danger' },
        inactive: { label: 'Not running', variant: 'neutral' }
    };

    let snapshots = $state(null);
    let failed = $state(null);
    let updatedAt = $state(null);
    let dialog = $state(null);

    let inFlight = false;
    let controller = null;

    function tone(percent) {
        if (percent == null) return 'none';
        if (percent < 50) return 'ok';
        if (percent < 80) return 'warn';
        return 'high';
    }

    function gb(value) {
        if (value == null) return '-';
        return Number.isInteger(value) ? String(value) : value.toFixed(1);
    }

    function pct(value) {
        return value == null ? '-' : `${Math.round(value)}%`;
    }

    function memory(used, total) {
        return used == null ? `${gb(total)} GB total` : `${gb(used)} / ${gb(total)} GB`;
    }

    function statusOf(snapshot) {
        return STATUS[snapshot.status] ?? STATUS.error;
    }

    async function load() {
        if (inFlight || (typeof document !== 'undefined' && document.hidden)) return;
        inFlight = true;
        controller = new AbortController();
        try {
            const response = await fetch(`${context.apiBaseUrl}/api/system/backends`, {
                headers: { Authorization: `Bearer ${context.token}` },
                signal: controller.signal
            });
            if (!response.ok) {
                failed = response.status === 403 ? 'denied' : 'unavailable';
                return;
            }
            const body = await response.json();
            snapshots = Array.isArray(body?.data) ? body.data : [];
            failed = null;
            updatedAt = new Date();
        } catch (error) {
            if (error?.name !== 'AbortError') failed = 'unavailable';
        } finally {
            inFlight = false;
        }
    }

    function onkeydown(event) {
        if (event.key === 'Escape') {
            event.stopPropagation();
            onclose?.();
        }
    }

    $effect(() => {
        load();
        const timer = setInterval(load, POLL_MS);
        dialog?.focus();
        return () => {
            clearInterval(timer);
            controller?.abort();
        };
    });

    function portal(node) {
        document.body.appendChild(node);
        return {
            destroy() {
                node.remove();
            }
        };
    }
</script>

<svelte:window {onkeydown} />

<div use:portal class="backdrop" role="presentation" onclick={(event) => event.target === event.currentTarget && onclose?.()}>
    <div
        bind:this={dialog}
        class="panel"
        role="dialog"
        aria-modal="true"
        aria-label="Backends"
        tabindex="-1"
    >
        <header class="panel-header">
            <div class="panel-heading">
                <h2>Backends</h2>
                <p>
                    {#if failed && snapshots}
                        Showing the last reading, updates are failing
                    {:else if updatedAt}
                        Live, updated <span class="mono">{updatedAt.toLocaleTimeString()}</span>
                    {:else}
                        Reading every enabled backend
                    {/if}
                </p>
            </div>
            <HostUi name="Tooltip" props={{ text: 'Close', position: 'bottom' }}>
                <button type="button" class="close" aria-label="Close backends panel" onclick={() => onclose?.()}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M6 6l12 12M18 6L6 18" />
                    </svg>
                </button>
            </HostUi>
        </header>

        <div class="panel-body">
            {#if snapshots === null && failed === null}
                <p class="note" role="status">Reading backends...</p>
            {:else if snapshots === null}
                <p class="note" role="alert">
                    {failed === 'denied'
                        ? 'Only administrators can read per-backend hardware.'
                        : 'The backends could not be read. Trying again in a few seconds.'}
                </p>
            {:else if snapshots.length === 0}
                <p class="note">No backend is enabled. Enable one in Admin, Backends.</p>
            {:else}
                {#if failed}
                    <p class="note stale" role="alert">The last update failed, so these numbers may be out of date.</p>
                {/if}
                <ul class="backends">
                    {#each snapshots as snapshot (snapshot.id)}
                        {@const status = statusOf(snapshot)}
                        <li class="backend" data-testid={`backend-${snapshot.id}`} data-status={snapshot.status}>
                            <div class="backend-head">
                                <div class="backend-title">
                                    <strong>{snapshot.name}</strong>
                                    <span>{KIND_LABEL[snapshot.kind] ?? KIND_LABEL.other}</span>
                                </div>
                                <HostUi name="Badge" props={{ variant: status.variant, dot: true }}>{status.label}</HostUi>
                            </div>

                            {#if snapshot.detail && snapshot.status !== 'online'}
                                <p class="detail">{snapshot.detail}</p>
                            {/if}

                            {#if snapshot.status !== 'inactive'}
                                <dl class="jobs">
                                    <div><dt>Running</dt><dd class="mono">{snapshot.jobs.running}{snapshot.jobs.capacity ? ` / ${snapshot.jobs.capacity}` : ''}</dd></div>
                                    <div><dt>Queued</dt><dd class="mono">{snapshot.jobs.queued}</dd></div>
                                </dl>
                            {/if}

                            {#if snapshot.hardware}
                                <div class="meters">
                                    {#if snapshot.hardware.cpu}
                                        <div class="meter" data-tone={tone(snapshot.hardware.cpu.usage_percent)}>
                                            <span class="meter-label">CPU</span>
                                            <span class="meter-value mono">
                                                {snapshot.hardware.cpu.usage_percent == null
                                                    ? `${snapshot.hardware.cpu.cores} cores`
                                                    : `${pct(snapshot.hardware.cpu.usage_percent)} of ${snapshot.hardware.cpu.cores} cores`}
                                            </span>
                                            <span class="bar"><span style={`width: ${snapshot.hardware.cpu.usage_percent ?? 0}%; min-width: ${snapshot.hardware.cpu.usage_percent == null ? 0 : 2}px`}></span></span>
                                        </div>
                                    {/if}
                                    {#if snapshot.hardware.ram}
                                        <div class="meter" data-tone={tone(snapshot.hardware.ram.usage_percent)}>
                                            <span class="meter-label">RAM</span>
                                            <span class="meter-value mono">{memory(snapshot.hardware.ram.used_gb, snapshot.hardware.ram.total_gb)}</span>
                                            <span class="bar"><span style={`width: ${snapshot.hardware.ram.usage_percent ?? 0}%; min-width: ${snapshot.hardware.ram.usage_percent == null ? 0 : 2}px`}></span></span>
                                        </div>
                                    {/if}
                                    {#each snapshot.hardware.gpus as gpu (gpu.index)}
                                        <div class="meter" data-tone={tone(gpu.vram_usage_percent)}>
                                            <span class="meter-label">GPU {gpu.index}</span>
                                            <span class="meter-value mono">{memory(gpu.vram_used_gb, gpu.vram_total_gb)}</span>
                                            <span class="bar"><span style={`width: ${gpu.vram_usage_percent ?? 0}%; min-width: ${gpu.vram_usage_percent == null ? 0 : 2}px`}></span></span>
                                            <span class="meter-sub">
                                                <span class="gpu-name">{gpu.name}</span>
                                                {#if gpu.utilization_percent != null || gpu.temperature_c != null}
                                                    <span class="mono">
                                                        {#if gpu.utilization_percent != null}{pct(gpu.utilization_percent)}{/if}
                                                        {#if gpu.temperature_c != null} {Math.round(gpu.temperature_c)}&deg;C{/if}
                                                    </span>
                                                {/if}
                                            </span>
                                        </div>
                                    {/each}
                                </div>
                            {:else if snapshot.kind === 'cloud' && snapshot.status !== 'inactive'}
                                <p class="note-inline">Runs on the provider's hardware, so there is nothing to measure here.</p>
                            {/if}
                        </li>
                    {/each}
                </ul>
            {/if}
        </div>
    </div>
</div>

<style>
    .backdrop {
        position: fixed;
        inset: 0;
        z-index: var(--z-overlay, 1000);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
        background: rgb(0 0 0 / 0.55);
    }

    .panel {
        display: flex;
        flex-direction: column;
        width: min(760px, 100%);
        max-height: min(720px, 100%);
        overflow: hidden;
        color: rgb(var(--fg));
        background: rgb(var(--surface-1));
        border: 1px solid rgb(var(--line-strong));
        border-radius: 10px;
        box-shadow: var(--shadow-overlay);
        font-family: inherit;
        outline: none;
    }

    .panel-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        padding: 16px 16px 12px;
        border-bottom: 1px solid rgb(var(--line));
    }

    .panel-heading h2 {
        margin: 0;
        font-size: 16px;
        line-height: 22px;
        font-weight: 600;
    }

    .panel-heading p {
        margin: 2px 0 0;
        font-size: 12px;
        line-height: 16px;
        color: rgb(var(--fg-subtle));
    }

    .close {
        display: grid;
        width: 28px;
        height: 28px;
        place-items: center;
        padding: 0;
        color: rgb(var(--fg-muted));
        background: transparent;
        border: 0;
        border-radius: 4px;
        cursor: pointer;
    }

    .close:hover {
        color: rgb(var(--fg));
        background: rgb(var(--surface-2));
    }

    .close:focus-visible {
        outline: 2px solid rgb(var(--signal));
        outline-offset: 1px;
    }

    .close svg {
        width: 16px;
        height: 16px;
    }

    .panel-body {
        flex: 1 1 auto;
        min-height: 0;
        padding: 12px 16px 16px;
        overflow-y: auto;
    }

    .note {
        margin: 24px 0;
        text-align: center;
        font-size: 14px;
        line-height: 20px;
        color: rgb(var(--fg-muted));
    }

    .note.stale {
        margin: 0 0 12px;
        padding: 8px 12px;
        text-align: left;
        font-size: 13px;
        color: rgb(var(--warning));
        background: rgb(var(--warning) / 0.1);
        border: 1px solid rgb(var(--warning) / 0.25);
        border-radius: 4px;
    }

    .backends {
        display: flex;
        flex-direction: column;
        gap: 12px;
        margin: 0;
        padding: 0;
        list-style: none;
    }

    .backend {
        padding: 14px;
        background: rgb(var(--surface-2));
        border: 1px solid rgb(var(--line));
        border-radius: 6px;
    }

    .backend[data-status='unreachable'],
    .backend[data-status='offline'],
    .backend[data-status='inactive'] {
        opacity: 0.9;
    }

    .backend-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
    }

    .backend-title {
        display: flex;
        min-width: 0;
        flex-direction: column;
    }

    .backend-title strong {
        overflow: hidden;
        font-size: 14px;
        line-height: 20px;
        font-weight: 600;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .backend-title span {
        font-size: 12px;
        line-height: 16px;
        color: rgb(var(--fg-subtle));
    }

    .detail {
        margin: 8px 0 0;
        font-size: 13px;
        line-height: 18px;
        color: rgb(var(--fg-muted));
        overflow-wrap: anywhere;
    }

    .jobs {
        display: flex;
        gap: 20px;
        margin: 10px 0 0;
    }

    .jobs div {
        display: flex;
        align-items: baseline;
        gap: 6px;
    }

    .jobs dt {
        font-size: 12px;
        color: rgb(var(--fg-subtle));
    }

    .jobs dd {
        margin: 0;
        font-size: 13px;
        color: rgb(var(--fg));
    }

    .meters {
        display: flex;
        flex-direction: column;
        gap: 12px;
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px solid rgb(var(--line));
    }

    .meter {
        display: grid;
        grid-template-columns: 5rem minmax(0, 1fr) auto;
        grid-template-areas:
            'label bar value'
            '. sub sub';
        align-items: center;
        column-gap: 12px;
        row-gap: 2px;
        --meter-color: rgb(var(--fg-subtle));
    }

    .meter[data-tone='ok'] {
        --meter-color: rgb(var(--success));
    }

    .meter[data-tone='warn'] {
        --meter-color: rgb(var(--warning));
    }

    .meter[data-tone='high'] {
        --meter-color: rgb(var(--danger));
    }

    .meter-label {
        grid-area: label;
        font-size: 12px;
        line-height: 16px;
        color: rgb(var(--fg-muted));
    }

    .meter-value {
        grid-area: value;
        font-size: 12px;
        line-height: 16px;
        text-align: right;
        color: rgb(var(--fg));
    }

    .bar {
        grid-area: bar;
        height: 6px;
        overflow: hidden;
        background: rgb(var(--surface-3));
        border-radius: 3px;
    }

    .bar span {
        display: block;
        height: 100%;
        max-width: 100%;
        background: var(--meter-color);
        border-radius: inherit;
        transition: width 300ms ease;
    }

    .meter-sub {
        grid-area: sub;
        display: flex;
        justify-content: space-between;
        gap: 12px;
        min-width: 0;
        font-size: 12px;
        line-height: 16px;
        color: rgb(var(--fg-subtle));
    }

    .gpu-name {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .note-inline {
        margin: 10px 0 0;
        font-size: 13px;
        line-height: 18px;
        color: rgb(var(--fg-subtle));
    }

    .mono {
        font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
        font-variant-numeric: tabular-nums;
    }

    @media (max-width: 560px) {
        .backdrop {
            align-items: flex-end;
            padding: 0;
        }

        .panel {
            width: 100%;
            max-height: 92%;
            border-radius: 10px 10px 0 0;
        }

        .meter {
            grid-template-columns: minmax(0, 1fr) auto;
            grid-template-areas:
                'label value'
                'bar bar'
                'sub sub';
        }
    }
</style>
