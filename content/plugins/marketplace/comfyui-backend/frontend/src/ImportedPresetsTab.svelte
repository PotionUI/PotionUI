<script>
	// Registered as this plugin's `admin_tabs[]` "Imported presets" tab (see
	// manifest.yml) - mounted with `{ pluginId, plugin }` props. Lists every
	// preset under content/presets/local this plugin's importer created (see
	// backend/api.py's GET /presets/imported) and offers row actions: reload
	// (re-emit from the stored source), edit (reopens the wizard, prefilled),
	// delete, and Open in Presets.
	//
	// Edit hands off to the sibling "Import workflow" tab - a SEPARATELY
	// mounted plugin dist bundle, so there is no shared module state between
	// them (esbuild inlines a shared .js import into each bundle
	// independently). `sessionStorage` plus a `window` CustomEvent is this
	// plugin's own small cross-tab bridge: sessionStorage carries which
	// preset to edit (just the id - the wizard re-fetches everything else
	// fresh), the event asks the host (PluginsTab.svelte's generic
	// `potionui:switch-plugin-tab` listener) to switch to that tab.
	import { onMount } from 'svelte';

	let { pluginId = 'comfyui-backend', plugin = null } = $props();

	const API_BASE = `/api/plugins/${pluginId}`;
	const EDIT_STORAGE_KEY = 'comfyui-import-edit-preset-id';

	let loading = $state(true);
	let loadError = $state('');
	let presets = $state([]);

	let reloadingId = $state(null);
	let reloadResultById = $state({});
	let deletingId = $state(null);
	let deleteError = $state('');
	let confirmDeleteTarget = $state(null);

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

	async function reloadPreset(p) {
		if (reloadingId) return;
		reloadingId = p.preset_id;
		reloadResultById = { ...reloadResultById, [p.preset_id]: null };
		try {
			const res = await fetch(`${API_BASE}/presets/imported/${p.preset_id}/reload`, {
				method: 'POST',
				credentials: 'include',
				headers: authHeaders()
			});
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				reloadResultById = {
					...reloadResultById,
					[p.preset_id]: { tone: 'danger', text: payload?.detail || payload?.message || `Reload failed (${res.status})` }
				};
				return;
			}
			const errCount = payload.lint.errors.length;
			const warnCount = payload.lint.warnings.length;
			const tone = errCount > 0 ? 'danger' : warnCount > 0 ? 'warn' : 'ok';
			const text =
				errCount > 0
					? `${errCount} lint error${errCount === 1 ? '' : 's'}`
					: warnCount > 0
						? `Reloaded — ${warnCount} warning${warnCount === 1 ? '' : 's'}`
						: 'Reloaded — lint clean';
			reloadResultById = { ...reloadResultById, [p.preset_id]: { tone, text } };
			await load();
		} catch (e) {
			reloadResultById = { ...reloadResultById, [p.preset_id]: { tone: 'danger', text: 'Could not reach the server.' } };
		} finally {
			reloadingId = null;
		}
	}

	function editPreset(p) {
		try {
			sessionStorage.setItem(EDIT_STORAGE_KEY, p.preset_id);
		} catch (e) {
			// Best-effort - a private-window sessionStorage failure just means
			// the wizard opens on a blank Source step instead of prefilled.
		}
		window.dispatchEvent(
			new CustomEvent('potionui:switch-plugin-tab', { detail: { pluginId, tabId: 'import-workflow' } })
		);
	}

	function askDelete(p) {
		confirmDeleteTarget = p;
		deleteError = '';
	}

	function cancelDelete() {
		confirmDeleteTarget = null;
		deleteError = '';
	}

	async function confirmDelete() {
		if (!confirmDeleteTarget || deletingId) return;
		const target = confirmDeleteTarget;
		deletingId = target.preset_id;
		deleteError = '';
		try {
			const res = await fetch(`${API_BASE}/presets/imported/${target.preset_id}`, {
				method: 'DELETE',
				credentials: 'include',
				headers: authHeaders()
			});
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				deleteError = payload?.detail || payload?.message || `Delete failed (${res.status})`;
				return;
			}
			presets = presets.filter((p) => p.preset_id !== target.preset_id);
			confirmDeleteTarget = null;
		} catch (e) {
			deleteError = 'Could not reach the server.';
		} finally {
			deletingId = null;
		}
	}

	function handleModalKeydown(e) {
		if (e.key === 'Escape') {
			e.preventDefault();
			cancelDelete();
		} else if (e.key === 'Enter') {
			e.preventDefault();
			confirmDelete();
		}
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
				{@const reloadResult = reloadResultById[p.preset_id]}
				<div class="ip-row">
					<span class="ip-name">{p.name}</span>
					<span class="ip-fam mono">{p.family} · {p.variant}</span>
					<span class="chip chip-mute">{p.format}</span>
					<span class="chip {chip.className} mono">{chip.text}</span>
					<span class="ip-created mono">{relativeTime(p.created_at)}</span>
					<div class="ip-actions">
						<button
							type="button"
							class="iconbtn"
							data-tip="Reload from source"
							aria-label="Reload from source"
							disabled={reloadingId === p.preset_id}
							onclick={() => reloadPreset(p)}
						>
							{#if reloadingId === p.preset_id}
								<span class="spinner" aria-hidden="true"></span>
							{:else}
								<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
									<polyline points="23 4 23 10 17 10" />
									<polyline points="1 20 1 14 7 14" />
									<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
								</svg>
							{/if}
						</button>
						<button type="button" class="iconbtn" data-tip="Edit" aria-label="Edit" onclick={() => editPreset(p)}>
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
								<path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
								<path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
							</svg>
						</button>
						<button
							type="button"
							class="iconbtn iconbtn-danger"
							data-tip="Delete"
							aria-label="Delete"
							onclick={() => askDelete(p)}
						>
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
								<polyline points="3 6 5 6 21 6" />
								<path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
							</svg>
						</button>
						<a
							class="iconbtn"
							data-tip="Open in Presets"
							aria-label="Open in Presets"
							href={`/admin?tab=presets&preset=${p.preset_id}`}
						>
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
								<path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
								<polyline points="15 3 21 3 21 9" />
								<line x1="10" y1="14" x2="21" y2="3" />
							</svg>
						</a>
					</div>
					{#if reloadResult}
						<div class="ip-reload-result ip-reload-{reloadResult.tone}">{reloadResult.text}</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if confirmDeleteTarget}
	<div
		class="overlay"
		role="button"
		tabindex="-1"
		aria-label="Close dialog"
		onclick={(e) => e.target === e.currentTarget && cancelDelete()}
		onkeydown={() => {}}
	>
		<div class="dialog" role="dialog" aria-modal="true" aria-label="Delete imported preset">
			<h2>Delete "{confirmDeleteTarget.name}"?</h2>
			<p class="dialog-body">
				This removes <span class="mono">{confirmDeleteTarget.family}/{confirmDeleteTarget.variant}</span> from content/presets/local
				permanently. This can't be undone.
			</p>
			{#if deleteError}
				<p class="message message-error">{deleteError}</p>
			{/if}
			<div class="dialog-footer">
				<button type="button" class="btn btn-secondary" disabled={!!deletingId} onclick={cancelDelete}>Cancel</button>
				<button type="button" class="btn btn-danger" disabled={!!deletingId} onclick={confirmDelete}>
					{deletingId ? 'Deleting…' : 'Delete'}
				</button>
			</div>
		</div>
	</div>
{/if}

<svelte:window onkeydown={confirmDeleteTarget ? handleModalKeydown : undefined} />

<style>
	.ip-wrap {
		width: 100%;
		max-width: none;
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
		flex-shrink: 0;
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
		/* Not `overflow: hidden` - that clipped the [data-tip] tooltips on the
		   first/last row. .ip-head carries its own corner radius instead. */
	}
	.ip-row {
		display: grid;
		grid-template-columns: 2fr 1fr 90px 90px 100px 128px;
		align-items: center;
		gap: 14px;
		padding: 12px 16px;
	}
	.ip-row + .ip-row {
		border-top: 1px solid rgb(var(--line, 36 38 44));
	}
	.ip-head {
		border-radius: 6px 6px 0 0;
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
	.ip-reload-result {
		grid-column: 1 / -1;
		margin-top: 8px;
		padding: 6px 10px;
		border-radius: 4px;
		font-size: 11.5px;
		width: fit-content;
	}
	.ip-reload-ok {
		background: rgb(var(--success, 61 214 140) / 0.1);
		color: rgb(var(--success, 61 214 140));
	}
	.ip-reload-warn {
		background: rgb(var(--warning, 255 197 61) / 0.1);
		color: rgb(var(--warning, 255 197 61));
	}
	.ip-reload-danger {
		background: rgb(var(--danger, 255 138 138) / 0.1);
		color: rgb(var(--danger, 255 138 138));
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

	.ip-actions {
		display: flex;
		align-items: center;
		gap: 4px;
		justify-content: flex-end;
	}

	[data-tip] {
		position: relative;
	}
	[data-tip]::after {
		content: attr(data-tip);
		position: absolute;
		bottom: calc(100% + 7px);
		left: 50%;
		transform: translateX(-50%);
		background: rgb(var(--surface-3, 39 42 49));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11px;
		padding: 4px 7px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		white-space: nowrap;
		opacity: 0;
		pointer-events: none;
		transition: opacity 0.1s;
		z-index: 30;
	}
	[data-tip]:hover::after {
		opacity: 1;
	}

	.iconbtn {
		width: 26px;
		height: 26px;
		flex-shrink: 0;
		border-radius: 4px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		color: rgb(var(--fg-subtle, 122 128 144));
		background: transparent;
		border: 1px solid transparent;
		cursor: pointer;
		text-decoration: none;
	}
	.iconbtn:hover {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-2, 31 33 38));
	}
	.iconbtn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.iconbtn-danger:hover {
		color: rgb(var(--danger, 255 138 138));
		background: rgb(var(--danger, 255 138 138) / 0.1);
	}

	.overlay {
		position: fixed;
		inset: 0;
		z-index: 9999;
		display: flex;
		align-items: center;
		justify-content: center;
		background: rgb(0 0 0 / 0.6);
		backdrop-filter: blur(4px);
	}
	.dialog {
		width: min(420px, calc(100vw - 32px));
		padding: 20px;
		background: rgb(var(--surface-1, 22 24 28));
		border-radius: 10px;
		box-shadow: 0 16px 48px rgb(0 0 0 / 0.6);
	}
	.dialog h2 {
		margin: 0 0 8px;
		color: rgb(var(--fg, 232 234 237));
		font-size: 15px;
		font-weight: 600;
	}
	.dialog-body {
		margin: 0 0 16px;
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 12.5px;
		line-height: 1.5;
	}
	.dialog-footer {
		display: flex;
		justify-content: flex-end;
		gap: 10px;
	}

	.btn {
		height: 30px;
		padding: 0 14px;
		border-radius: 4px;
		font-size: 12.5px;
		font-weight: 600;
		display: inline-flex;
		align-items: center;
		border: 1px solid transparent;
		cursor: pointer;
	}
	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn-secondary {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-2, 31 33 38));
		border-color: rgb(var(--line-strong, 43 46 53));
	}
	.btn-secondary:hover:not(:disabled) {
		background: rgb(var(--surface-3, 39 42 49));
	}
	.btn-danger {
		color: rgb(var(--accent-contrast, 22 22 22));
		background: rgb(var(--danger, 255 138 138));
	}
	.btn-danger:hover:not(:disabled) {
		opacity: 0.9;
	}
</style>
