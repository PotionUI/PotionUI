<script>
	import { untrack } from 'svelte';
	import {
		USER_MODE_ALL,
		USER_MODE_SELECTED,
		buildInitialFormState,
		clampLimit,
		buildFetchRequestBody,
		isFormValid,
		summarizeFetchResult
	} from './promptsFetch.js';

	let { context = {}, hookName = '', pluginId = 'civitai-provider' } = $props();

	let model = $derived(context.model || {});

	let open = $state(false);
	let phase = $state('form');
	let loadingModal = $state(false);
	let loadError = $state('');
	let submitError = $state('');
	let options = $state(null);
	let users = $state([]);
	let currentUserId = $state(null);
	let formState = $state(null);
	let result = $state(null);
	let hoverTip = $state(null);

	let modalHostEl = $state(null);
	let modalApi = null;
	let modalInstance = null;
	let bodyEl = $state(null);

	let userPickerEl = $state(null);
	let userPickerApi = null;
	let userPickerInstance = null;

	let usersById = $derived(Object.fromEntries(users.map((u) => [u.id, u])));

	function authHeaders() {
		const token = localStorage.getItem('auth_token');
		return token ? { Authorization: `Bearer ${token}` } : {};
	}

	function notify(level, message) {
		const notifications = window.__potionui?.notifications;
		if (notifications?.toast) notifications.toast(level, message);
	}

	function userLabel(userId) {
		return usersById[userId]?.username || userId;
	}

	async function fetchJson(url, init) {
		const response = await fetch(url, {
			...init,
			headers: { ...authHeaders(), ...(init?.headers || {}) }
		});
		const payload = await response.json().catch(() => null);
		if (!response.ok || payload?.success === false) {
			const message = payload?.message || payload?.error || `Request failed (${response.status})`;
			throw new Error(message);
		}
		return payload?.data ?? null;
	}

	async function loadModalData() {
		loadingModal = true;
		loadError = '';
		try {
			const [optionsData, meData, usersData] = await Promise.all([
				fetchJson(`/api/plugins/${pluginId}/prompts/options`),
				fetchJson('/api/auth/me'),
				fetchJson('/api/users')
			]);
			options = optionsData;
			users = Array.isArray(usersData) ? usersData : [];
			currentUserId = meData?.id ?? null;
			formState = buildInitialFormState(options, currentUserId);
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Could not load fetch options.';
		} finally {
			loadingModal = false;
		}
	}

	async function openModal(event) {
		event.stopPropagation();
		if (!model.id) return;
		if (!window.__potionui?.components?.PluginModal) {
			notify('error', 'Fetch prompts is unavailable right now.');
			return;
		}
		open = true;
		phase = 'form';
		submitError = '';
		result = null;
		await loadModalData();
	}

	function closeModal() {
		if (phase === 'submitting') return;
		open = false;
	}

	function pickerValueFromFormState(fs) {
		return fs.userMode === USER_MODE_ALL ? USER_MODE_ALL : fs.selectedUserIds;
	}

	function onUserPickerChange(next) {
		formState =
			next === USER_MODE_ALL
				? { ...formState, userMode: USER_MODE_ALL }
				: { ...formState, userMode: USER_MODE_SELECTED, selectedUserIds: next };
	}

	function userPickerProps() {
		return {
			users: users.map((u) => ({ id: u.id, username: u.username, email: u.email })),
			value: pickerValueFromFormState(formState),
			onChange: onUserPickerChange,
			disabled: phase === 'submitting'
		};
	}

	$effect(() => {
		const el = userPickerEl;
		if (!el) return;
		const api = window.__potionui?.components?.UserPicker;
		if (!api) return;
		userPickerApi = api;
		userPickerInstance = api.mount(el, untrack(userPickerProps));
		return () => {
			api.unmount(userPickerInstance);
			userPickerInstance = null;
		};
	});

	$effect(() => {
		if (!userPickerInstance || !userPickerApi || !formState) return;
		userPickerApi.update(userPickerInstance, userPickerProps());
	});

	function modalProps() {
		const base = {
			title: 'Fetch prompts',
			subtitle: model.name || model.filename || 'Model',
			onCancel: closeModal,
			mountBody: (el) => {
				el.appendChild(bodyEl);
			}
		};
		if (phase === 'result') {
			return {
				...base,
				confirmLabel: 'Close',
				hideCancel: true,
				onConfirm: closeModal
			};
		}
		return {
			...base,
			confirmLabel: 'Fetch prompts',
			confirmDisabled: !formState || !isFormValid(formState) || loadingModal || !!loadError,
			busy: phase === 'submitting',
			onConfirm: submit
		};
	}

	$effect(() => {
		const el = modalHostEl;
		if (!el || !open) return;
		const api = window.__potionui?.components?.PluginModal;
		if (!api) return;
		modalApi = api;
		modalInstance = api.mount(el, untrack(modalProps));
		return () => {
			api.unmount(modalInstance);
			modalInstance = null;
		};
	});

	$effect(() => {
		if (!modalInstance || !modalApi) return;
		modalApi.update(modalInstance, modalProps());
	});

	function onLimitInput(e) {
		formState = { ...formState, limit: clampLimit(e.currentTarget.value, options?.limit_max) };
	}

	async function submit() {
		if (!formState || !isFormValid(formState) || phase === 'submitting') return;
		phase = 'submitting';
		submitError = '';
		try {
			const body = buildFetchRequestBody(model.id, formState);
			const data = await fetchJson(`/api/plugins/${pluginId}/prompts/fetch`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body)
			});
			result = summarizeFetchResult(data);
			phase = 'result';
			context.refresh?.();
		} catch (e) {
			submitError = e instanceof Error ? e.message : 'Fetching prompts failed.';
			phase = 'form';
			notify('error', 'Fetching CivitAI prompts failed');
		}
	}

	function showTip(e) {
		const text = e.currentTarget.dataset.tip;
		if (!text) return;
		const rect = e.currentTarget.getBoundingClientRect();
		hoverTip = { text, top: rect.top - 6, left: rect.left + rect.width / 2 };
	}

	function hideTip() {
		hoverTip = null;
	}

	function optionValue(o) {
		return typeof o === 'string' ? o : (o?.value ?? '');
	}

	function optionLabel(o) {
		return typeof o === 'string' ? o : (o?.label ?? String(o?.value ?? ''));
	}
</script>

{#if model.id}
	<button
		class="bg-black/60 hover:bg-black/80 text-white rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 opacity-0 group-hover:opacity-100"
		onclick={openModal}
		aria-label="Fetch prompts from CivitAI"
	>
		<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path
				stroke-linecap="round"
				stroke-linejoin="round"
				stroke-width="2"
				d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
			/>
		</svg>
	</button>
{/if}

{#if open}
	<div bind:this={modalHostEl}></div>
	<div bind:this={bodyEl} class="body">
		{#if loadingModal}
		<div class="loading-row"><span class="spinner"></span>Loading options&hellip;</div>
		{:else if loadError}
		<p class="message message-error">{loadError}</p>
		{:else if phase === 'result' && result}
		<div class="result-list">
			{#each result.models as m (m.modelId)}
				<div class="result-model">
					<div class="result-model-header">
						<span
							class="result-model-name"
							role="note"
							data-tip={m.modelName}
							onmouseenter={showTip}
							onmouseleave={hideTip}
						>
							{m.modelName}
						</span>
						{#if m.notLinked}
							<span class="chip chip-warn">Not linked</span>
						{:else if m.error}
							<span class="chip chip-danger">Error</span>
						{/if}
					</div>

					{#if m.notLinked}
						<p class="result-hint">Run Fetch CivitAI info first.</p>
					{:else if m.error}
						<p class="result-hint result-hint-danger">{m.error}</p>
					{:else}
						<div class="result-stats">
							<div class="stat">
								<span class="stat-label">Images seen</span>
								<span class="stat-value">{m.imagesSeen}</span>
							</div>
							<div class="stat">
								<span class="stat-label">With prompt</span>
								<span class="stat-value">{m.withPrompt}</span>
							</div>
							<div class="stat">
								<span class="stat-label">Created</span>
								<span class="stat-value">{m.created}</span>
							</div>
							<div class="stat">
								<span class="stat-label">Skipped duplicates</span>
								<span class="stat-value">{m.skippedDuplicates}</span>
							</div>
						</div>

						{#if m.perUserRows.length}
							<table class="per-user-table">
								<thead>
									<tr>
										<th>User</th>
										<th>Created</th>
										<th>Skipped</th>
									</tr>
								</thead>
								<tbody>
									{#each m.perUserRows as row (row.userId)}
										<tr>
											<td class="per-user-name">
												<span role="note" data-tip={userLabel(row.userId)} onmouseenter={showTip} onmouseleave={hideTip}>
													{userLabel(row.userId)}
												</span>
											</td>
											<td>{row.created}</td>
											<td>{row.skippedDuplicates}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						{/if}
					{/if}
				</div>
			{/each}
		</div>
		{:else if formState}
		{#if submitError}
			<p class="message message-error">{submitError}</p>
		{/if}

		<div class="field-group">
			<span class="label">Users</span>
			<div bind:this={userPickerEl}></div>
		</div>

		<div class="field-grid">
			<div class="field-group">
				<span class="label">Sort</span>
				<select class="input" bind:value={formState.sort} disabled={phase === 'submitting'}>
					{#each options?.sorts || [] as o}
						<option value={optionValue(o)}>{optionLabel(o)}</option>
					{/each}
				</select>
			</div>
			<div class="field-group">
				<span class="label">Period</span>
				<select class="input" bind:value={formState.period} disabled={phase === 'submitting'}>
					{#each options?.periods || [] as o}
						<option value={optionValue(o)}>{optionLabel(o)}</option>
					{/each}
				</select>
			</div>
			<div class="field-group">
				<span class="label">NSFW level</span>
				<select class="input" bind:value={formState.nsfw} disabled={phase === 'submitting'}>
					<option value={null}>Any</option>
					{#each options?.nsfw || [] as o}
						<option value={optionValue(o)}>{optionLabel(o)}</option>
					{/each}
				</select>
			</div>
			<div class="field-group">
				<span class="label">Limit</span>
				<input
					class="input"
					type="number"
					min="1"
					max={options?.limit_max ?? 200}
					value={formState.limit}
					oninput={onLimitInput}
					disabled={phase === 'submitting'}
				/>
			</div>
		</div>

		<div class="field-group">
			<label class="toggle-row">
				<span class="toggle-switch" class:toggle-on={formState.includeShowcase}>
					<input
						type="checkbox"
						checked={formState.includeShowcase}
						onchange={(e) => (formState = { ...formState, includeShowcase: e.currentTarget.checked })}
						disabled={phase === 'submitting'}
					/>
					<span class="toggle-thumb"></span>
				</span>
				Include creator showcase images
			</label>
			<label class="toggle-row">
				<span class="toggle-switch" class:toggle-on={formState.includeNegative}>
					<input
						type="checkbox"
						checked={formState.includeNegative}
						onchange={(e) => (formState = { ...formState, includeNegative: e.currentTarget.checked })}
						disabled={phase === 'submitting'}
					/>
					<span class="toggle-thumb"></span>
				</span>
				Also save negative prompts
			</label>
		</div>
		{/if}
	</div>
{/if}

{#if hoverTip}
	<div class="hover-tip" style={`top:${hoverTip.top}px; left:${hoverTip.left}px;`}>{hoverTip.text}</div>
{/if}

<style>
	.body {
		display: flex;
		flex-direction: column;
		gap: 18px;
	}

	.label {
		display: block;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 12px;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin-bottom: 6px;
	}
	.field-group {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.field-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 14px;
	}
	.toggle-row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		color: rgb(var(--fg, 232 234 237));
		cursor: pointer;
	}
	.result-model-name,
	.per-user-name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}

	.input {
		width: 100%;
		min-height: 32px;
		padding: 6px 10px;
		font-size: 13px;
		background: rgb(var(--field-bg, 31 33 38));
		border: 1px solid rgb(var(--field-border, 43 46 53));
		color: rgb(var(--fg, 232 234 237));
		border-radius: 4px;
	}
	.input:focus {
		outline: 2px solid rgb(var(--accent, 255 255 255));
		outline-offset: 1px;
	}
	.input:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.toggle-switch {
		position: relative;
		display: inline-flex;
		flex-shrink: 0;
		width: 34px;
		height: 18px;
		border-radius: 9999px;
		background: rgb(var(--surface-3, 39 42 49));
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		transition: background-color 0.1s;
	}
	.toggle-switch.toggle-on {
		background: rgb(var(--signal, 91 157 255));
		border-color: transparent;
	}
	.toggle-switch input {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		margin: 0;
		opacity: 0;
		cursor: pointer;
	}
	.toggle-thumb {
		position: absolute;
		top: 50%;
		left: 2px;
		transform: translateY(-50%);
		width: 13px;
		height: 13px;
		border-radius: 50%;
		background: rgb(var(--canvas, 12 13 15));
		transition: transform 0.1s;
	}
	.toggle-switch.toggle-on .toggle-thumb {
		transform: translateY(-50%) translateX(16px);
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
	.loading-row {
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

	.result-list {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.result-model {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		padding: 12px 14px;
		background: rgb(var(--surface-2, 31 33 38) / 0.4);
	}
	.result-model-header {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 8px;
	}
	.result-model-name {
		font-size: 13px;
		font-weight: 600;
		color: rgb(var(--fg, 232 234 237));
	}
	.result-hint {
		margin: 0;
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
	}
	.result-hint-danger {
		color: rgb(var(--danger, 255 138 138));
	}
	.result-stats {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 8px 16px;
	}
	.stat {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.stat-label {
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.stat-value {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-variant-numeric: tabular-nums;
		font-size: 14px;
		color: rgb(var(--fg, 232 234 237));
	}
	.chip {
		display: inline-flex;
		align-items: center;
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 3px;
	}
	.chip-warn {
		background: rgb(var(--warning, 255 197 61) / 0.1);
		color: rgb(var(--warning, 255 197 61));
	}
	.chip-danger {
		background: rgb(var(--danger, 255 138 138) / 0.1);
		color: rgb(var(--danger, 255 138 138));
	}
	.per-user-table {
		width: 100%;
		margin-top: 10px;
		border-collapse: collapse;
		font-size: 12px;
	}
	.per-user-table th {
		text-align: left;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle, 122 128 144));
		padding: 4px 8px 4px 0;
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
	}
	.per-user-table td {
		padding: 5px 8px 5px 0;
		color: rgb(var(--fg-muted, 169 174 184));
		border-bottom: 1px solid rgb(var(--line, 36 38 44) / 0.5);
	}
	.per-user-table td.per-user-name {
		max-width: 12rem;
		color: rgb(var(--fg, 232 234 237));
	}

	.hover-tip {
		position: fixed;
		z-index: 10000;
		transform: translate(-50%, -100%);
		padding: 4px 8px;
		font-size: 12px;
		font-weight: 500;
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-3, 39 42 49));
		border-radius: 4px;
		box-shadow: 0 4px 16px rgb(0 0 0 / 0.5);
		pointer-events: none;
		white-space: nowrap;
	}
</style>
