<script>
	// Registered as this plugin's `admin_tabs[]` "Imported presets" tab (see
	// manifest.yml) - mounted with `{ pluginId, plugin }` props (unused: this
	// component only ever talks to its own fixed endpoint). Lists every
	// preset under content/presets/local this plugin's importer created (see
	// backend/api.py's GET /presets/imported).
	import { onMount } from 'svelte';

	let { pluginId = 'comfyui-backend', plugin = null } = $props();

	const API_BASE = `/api/plugins/${pluginId}`;

	let loading = $state(true);
	let loadError = $state('');
	let presets = $state([]);

	function authHeaders() {
		const token = typeof localStorage !== 'undefined' ? localStorage.getItem('auth_token') : null;
		return token ? { Authorization: `Bearer ${token}` } : {};
	}

	async function load() {
		loading = true;
		loadError = '';
		try {
			const res = await fetch(`${API_BASE}/presets/imported`, { credentials: 'include', headers: authHeaders() });
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				loadError = payload?.detail || payload?.message || `Failed to load (${res.status})`;
				return;
			}
			presets = payload?.presets ?? [];
		} catch (e) {
			loadError = 'Could not reach the server.';
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function requirementsChip(summary) {
		if (!summary) return { text: '—', className: 'chip-mute' };
		const total = summary.ok + summary.missing + summary.unknown + summary.optional_missing;
		if (summary.missing > 0) return { text: `${summary.ok}/${total}`, className: 'chip-danger' };
		if (summary.unknown > 0 || summary.optional_missing > 0) return { text: `${summary.ok}/${total}`, className: 'chip-warn' };
		return { text: `${summary.ok}/${total}`, className: 'chip-ok' };
	}

	function relativeTime(unixSeconds) {
		const deltaMs = Date.now() - unixSeconds * 1000;
		const minute = 60_000;
		const hour = 60 * minute;
		const day = 24 * hour;
		if (deltaMs < minute) return 'just now';
		if (deltaMs < hour) return `${Math.floor(deltaMs / minute)} min ago`;
		if (deltaMs < day) return `${Math.floor(deltaMs / hour)}h ago`;
		if (deltaMs < 30 * day) return `${Math.floor(deltaMs / day)}d ago`;
		return new Date(unixSeconds * 1000).toLocaleDateString();
	}
</script>

<div class="ip-wrap">
	{#if loading}
		<div class="ip-loading"><span class="spinner" aria-hidden="true"></span>Loading imported presets…</div>
	{:else if loadError}
		<p class="message message-error">{loadError}</p>
	{:else if presets.length === 0}
		<div class="ip-empty">
			No presets have been imported yet. Use the <strong>Import workflow</strong> tab to bring one in from ComfyUI.
		</div>
	{:else}
		<div class="ip-table" data-imported-presets>
			<div class="ip-row ip-head">
				<span>Name</span>
				<span>Family / variant</span>
				<span>Format</span>
				<span>Requirements</span>
				<span>Created</span>
				<span></span>
			</div>
			{#each presets as p (p.preset_id)}
				{@const chip = requirementsChip(p.requirements_summary)}
				<div class="ip-row">
					<span class="ip-name">{p.name}</span>
					<span class="ip-fam mono">{p.family} · {p.variant}</span>
					<span class="chip chip-mute">{p.format}</span>
					<span class="chip {chip.className} mono">{chip.text}</span>
					<span class="ip-created mono">{relativeTime(p.created_at)}</span>
					<a class="btn btn-secondary" href={`/admin?tab=presets&preset=${p.preset_id}`}>Open in Presets</a>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.ip-wrap {
		padding: 4px 0;
	}
	.mono {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-variant-numeric: tabular-nums;
	}
	.ip-loading {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 20px 0;
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 12px;
	}
	.spinner {
		width: 13px;
		height: 13px;
		border-radius: 50%;
		border: 2px solid rgb(var(--line-strong, 43 46 53));
		border-top-color: rgb(var(--signal, 91 157 255));
		animation: spin 0.7s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.ip-empty {
		padding: 20px;
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--surface-2, 31 33 38) / 0.4);
		color: rgb(var(--fg-subtle, 122 128 144));
		font-size: 12.5px;
		text-align: center;
	}
	.message {
		padding: 10px 14px;
		border-radius: 6px;
		font-size: 12px;
	}
	.message-error {
		color: rgb(var(--danger, 255 138 138));
		background: rgb(var(--danger, 255 138 138) / 0.1);
		border: 1px solid rgb(var(--danger, 255 138 138) / 0.25);
	}

	.ip-table {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--surface-1, 22 24 28));
		overflow: hidden;
	}
	.ip-row {
		display: grid;
		grid-template-columns: 2fr 1fr 90px 90px 100px 130px;
		align-items: center;
		gap: 14px;
		padding: 12px 16px;
	}
	.ip-row + .ip-row {
		border-top: 1px solid rgb(var(--line, 36 38 44));
	}
	.ip-head {
		background: rgb(var(--canvas, 12 13 15));
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle, 122 128 144));
		padding-top: 8px;
		padding-bottom: 8px;
	}
	.ip-name {
		font-size: 12.5px;
		font-weight: 500;
		color: rgb(var(--fg, 232 234 237));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.ip-fam {
		font-size: 11.5px;
		color: rgb(var(--fg-muted, 169 174 184));
	}
	.ip-created {
		font-size: 11px;
		color: rgb(var(--fg-subtle, 122 128 144));
	}

	.chip {
		display: inline-flex;
		align-items: center;
		width: fit-content;
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 3px;
	}
	.chip-mute {
		background: rgb(var(--surface-3, 39 42 49));
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.chip-ok {
		background: rgb(var(--success, 61 214 140) / 0.13);
		color: rgb(var(--success, 61 214 140));
	}
	.chip-warn {
		background: rgb(var(--warning, 255 197 61) / 0.13);
		color: rgb(var(--warning, 255 197 61));
	}
	.chip-danger {
		background: rgb(var(--danger, 255 138 138) / 0.13);
		color: rgb(var(--danger, 255 138 138));
	}

	.btn {
		height: 26px;
		padding: 0 10px;
		border-radius: 4px;
		font-size: 11.5px;
		font-weight: 600;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-1, 22 24 28));
		color: rgb(var(--fg-muted, 169 174 184));
		text-decoration: none;
		cursor: pointer;
		white-space: nowrap;
	}
	.btn:hover {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-2, 31 33 38));
	}
</style>
