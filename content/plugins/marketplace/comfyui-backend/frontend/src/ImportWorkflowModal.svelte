<script>
	// Plugin components can't import core Svelte components (BaseModal,
	// Button, Input, ConfirmFooter, confirmKeyboard, ...) - the compiled dist
	// bundles its own Svelte runtime and Tailwind isn't generated for
	// anything outside frontend/src - so the modal shell, the Esc/Enter
	// handling and every control are reproduced with the semantic design
	// tokens directly (see tokens.css), the same approach already used by
	// TextImportModal.
	import { onMount } from 'svelte';

	let { selectPreset, refreshPresets, onClose = () => {} } = $props();

	let rawText = $state('');
	let dragOver = $state(false);
	let fileInputEl = $state(null);

	let analyzing = $state(false);
	let analyzeError = $state('');
	let analysis = $state(null);
	let workflowJson = $state(null);

	let selectedKeys = $state(new Set());
	let fieldTypeByKey = $state({});
	let labelByKey = $state({});
	let moreOpen = $state(false);

	let fieldTypeOptions = $state([]);
	let families = $state([]);
	let modelFamily = $state('');
	let variant = $state('imported');
	let displayName = $state('');

	let creating = $state(false);
	let createError = $state('');
	let createResult = $state(null);

	let reviewing = $derived(analysis !== null);
	let obviousCandidates = $derived(analysis ? analysis.candidates.filter((c) => c.obvious) : []);
	let moreCandidates = $derived(analysis ? analysis.candidates.filter((c) => !c.obvious) : []);
	let canCreate = $derived(!!modelFamily.trim() && !!displayName.trim() && !creating);

	function candidateKey(c) {
		return `${c.node_id}:${c.input_name}`;
	}

	function authHeaders() {
		const token = typeof localStorage !== 'undefined' ? localStorage.getItem('auth_token') : null;
		return token ? { Authorization: `Bearer ${token}` } : {};
	}

	onMount(() => {
		(async () => {
			try {
				const res = await fetch('/api/fields/types', { credentials: 'include', headers: authHeaders() });
				if (res.ok) {
					const payload = await res.json();
					const list = payload?.data ?? [];
					fieldTypeOptions = [...new Set(list.filter((t) => !t.container).map((t) => t.type))].sort();
				}
			} catch (e) {
				// Best-effort - the select still works with whatever analyze suggested.
			}
			try {
				const res = await fetch('/api/plugins/comfyui-backend/presets/families', {
					credentials: 'include',
					headers: authHeaders()
				});
				if (res.ok) {
					const payload = await res.json();
					families = payload?.families ?? [];
				}
			} catch (e) {
				// Best-effort - the datalist is just a convenience.
			}
		})();
	});

	function readFile(file) {
		const reader = new FileReader();
		reader.onload = (e) => {
			rawText = e.target?.result ?? '';
			analyzeError = '';
		};
		reader.readAsText(file);
	}

	function handleFileInput(e) {
		const file = e.target.files?.[0];
		if (file) readFile(file);
	}

	function handleDrop(e) {
		e.preventDefault();
		dragOver = false;
		const file = e.dataTransfer?.files?.[0];
		if (file) readFile(file);
	}

	function handleDragOver(e) {
		e.preventDefault();
		dragOver = true;
	}

	function handleDragLeave() {
		dragOver = false;
	}

	async function runAnalyze() {
		if (analyzing) return;
		analyzeError = '';
		let parsed;
		try {
			parsed = JSON.parse(rawText);
		} catch (e) {
			analyzeError = 'That is not valid JSON.';
			return;
		}
		analyzing = true;
		try {
			const res = await fetch('/api/plugins/comfyui-backend/presets/import/analyze', {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json', ...authHeaders() },
				body: JSON.stringify({ workflow: parsed })
			});
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				analyzeError = payload?.detail || payload?.message || `Analyze failed (${res.status})`;
				return;
			}
			workflowJson = parsed;
			analysis = payload;
			const nextSelected = new Set();
			const nextTypes = {};
			const nextLabels = {};
			for (const c of payload.candidates) {
				const key = candidateKey(c);
				nextTypes[key] = c.suggested_field_type;
				nextLabels[key] = c.suggested_label;
				if (c.obvious) nextSelected.add(key);
			}
			selectedKeys = nextSelected;
			fieldTypeByKey = nextTypes;
			labelByKey = nextLabels;
		} catch (e) {
			analyzeError = 'Could not reach the server.';
		} finally {
			analyzing = false;
		}
	}

	function resetToPaste() {
		analysis = null;
		workflowJson = null;
		createResult = null;
		createError = '';
	}

	function toggleSelected(key) {
		const next = new Set(selectedKeys);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		selectedKeys = next;
	}

	async function runCreate() {
		if (!analysis || !workflowJson || !canCreate) return;
		createError = '';
		creating = true;
		try {
			const fields = analysis.candidates
				.filter((c) => selectedKeys.has(candidateKey(c)))
				.map((c) => {
					const key = candidateKey(c);
					return {
						node_id: c.node_id,
						input_name: c.input_name,
						field_type: fieldTypeByKey[key] || c.suggested_field_type,
						label: labelByKey[key] || c.suggested_label
					};
				});
			const res = await fetch('/api/plugins/comfyui-backend/presets/import', {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json', ...authHeaders() },
				body: JSON.stringify({
					workflow: workflowJson,
					fields,
					model_family: modelFamily.trim(),
					variant: variant.trim() || 'imported',
					display_name: displayName.trim()
				})
			});
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				createError = payload?.detail || payload?.message || `Import failed (${res.status})`;
				return;
			}
			createResult = payload;
		} catch (e) {
			createError = 'Could not reach the server.';
		} finally {
			creating = false;
		}
	}

	async function openInPresets() {
		if (!createResult) return;
		if (typeof refreshPresets === 'function') {
			try {
				await refreshPresets();
			} catch (e) {
				// Non-fatal - selectPreset still runs against whatever list is loaded.
			}
		}
		if (typeof selectPreset === 'function') selectPreset(createResult.preset_id);
		onClose();
	}

	function isEditableTarget(target) {
		let el = target;
		while (el) {
			const tag = el.tagName ? el.tagName.toUpperCase() : '';
			if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable) return true;
			el = el.parentElement;
		}
		return false;
	}

	function handleKeydown(e) {
		if (e.key === 'Escape') {
			if (creating || analyzing) return;
			e.preventDefault();
			onClose();
			return;
		}
		if (e.key === 'Enter' && !e.repeat && !isEditableTarget(e.target)) {
			e.preventDefault();
			if (createResult) return;
			if (!reviewing) runAnalyze();
			else if (canCreate) runCreate();
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#snippet candidateRow(c)}
	{@const key = candidateKey(c)}
	<label class="candidate-row" for={`import-cb-${key}`}>
		<input
			id={`import-cb-${key}`}
			type="checkbox"
			checked={selectedKeys.has(key)}
			onchange={() => toggleSelected(key)}
		/>
		<div class="candidate-node">
			<span class="candidate-title">{c.node_title || c.class_type}</span>
			<span class="candidate-input mono">{c.input_name}</span>
		</div>
		<span class="candidate-value mono" title={String(c.current_value)}>{String(c.current_value)}</span>
		<select
			class="candidate-type"
			value={fieldTypeByKey[key]}
			onchange={(e) => (fieldTypeByKey = { ...fieldTypeByKey, [key]: e.currentTarget.value })}
			onclick={(e) => e.stopPropagation()}
		>
			{#each [...new Set([fieldTypeByKey[key], ...fieldTypeOptions])] as opt}
				<option value={opt}>{opt}</option>
			{/each}
		</select>
		<input
			class="candidate-label"
			type="text"
			value={labelByKey[key]}
			oninput={(e) => (labelByKey = { ...labelByKey, [key]: e.currentTarget.value })}
			onclick={(e) => e.stopPropagation()}
		/>
	</label>
{/snippet}


<div class="overlay" role="button" tabindex="-1" aria-label="Close modal" onclick={(e) => e.target === e.currentTarget && !creating && !analyzing && onClose()} onkeydown={() => {}}>
	<div class="dialog" role="dialog" aria-modal="true" aria-label="Import ComfyUI workflow">
		<div class="header">
			<div class="header-title">
				<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" d="M12 3v12m0 0l-4-4m4 4l4-4M5 17v2a2 2 0 002 2h10a2 2 0 002-2v-2" />
				</svg>
				<h2>Import ComfyUI workflow</h2>
			</div>
			<button type="button" class="close-btn" aria-label="Close modal" disabled={creating || analyzing} onclick={onClose}>
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" d="M18 6L6 18M6 6l12 12" />
				</svg>
			</button>
		</div>

		<div class="body">
			{#if !reviewing}
				<p class="hint">
					Paste an <strong>Export (API)</strong> ComfyUI workflow, or drop the JSON file. Use ComfyUI's
					"Export (API)" menu item, not the regular workflow export.
				</p>

				<div
					class="dropzone"
					class:dragover={dragOver}
					ondrop={handleDrop}
					ondragover={handleDragOver}
					ondragleave={handleDragLeave}
					role="group"
					aria-label="Workflow JSON"
				>
					<textarea
						rows="12"
						placeholder={'{\n  "3": { "class_type": "KSampler", "inputs": { ... } },\n  ...\n}'}
						bind:value={rawText}
						oninput={() => (analyzeError = '')}
						data-import-json-input
					></textarea>
					<div class="dropzone-footer">
						<span class="dim">or</span>
						<button type="button" class="link-btn" onclick={() => fileInputEl?.click()}>choose a .json file</button>
						<input
							bind:this={fileInputEl}
							type="file"
							accept=".json,application/json"
							class="file-input-hidden"
							onchange={handleFileInput}
						/>
					</div>
				</div>

				{#if analyzeError}
					<p class="message message-error" data-import-analyze-error>{analyzeError}</p>
				{/if}
			{:else}
				<div class="detected-strip" data-import-detected>
					<span class="badge">{analysis.mode}</span>
					<span class="dim">·</span>
					<span class="mono">{analysis.node_count} nodes</span>
					{#if analysis.lora_chain}
						<span class="dim">·</span>
						<span class="badge badge-info">LoRA chain found</span>
					{/if}
					<button type="button" class="link-btn strip-end" onclick={resetToPaste} disabled={creating}>Change workflow</button>
				</div>

				{#if !createResult}
					<div class="candidate-list" data-import-candidates>
						{#each obviousCandidates as c (candidateKey(c))}
							{@render candidateRow(c)}
						{/each}

						{#if moreCandidates.length > 0}
							<button
								type="button"
								class="more-toggle"
								aria-expanded={moreOpen}
								onclick={() => (moreOpen = !moreOpen)}
							>
								<svg
									width="12"
									height="12"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="2"
									class="chevron"
									class:open={moreOpen}
									aria-hidden="true"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M9 6l6 6-6 6" />
								</svg>
								More inputs ({moreCandidates.length})
							</button>
							{#if moreOpen}
								{#each moreCandidates as c (candidateKey(c))}
									{@render candidateRow(c)}
								{/each}
							{/if}
						{/if}
					</div>

					<div class="name-grid">
						<div class="field">
							<label for="import-model-family">Model family</label>
							<input
								id="import-model-family"
								type="text"
								list="import-model-family-list"
								bind:value={modelFamily}
								placeholder="e.g. SDXL"
							/>
							<datalist id="import-model-family-list">
								{#each families as f}<option value={f}></option>{/each}
							</datalist>
						</div>
						<div class="field">
							<label for="import-variant">Variant</label>
							<input id="import-variant" type="text" bind:value={variant} placeholder="imported" />
						</div>
						<div class="field">
							<label for="import-display-name">Display name</label>
							<input id="import-display-name" type="text" bind:value={displayName} placeholder="e.g. SDXL - My workflow" />
						</div>
					</div>

					{#if createError}
						<p class="message message-error" data-import-create-error>{createError}</p>
					{/if}
				{:else}
					<div class="lint-result" data-import-lint>
						<p class="lint-heading">
							Preset created at <code class="mono">{createResult.path}</code>
						</p>
						{#if createResult.lint.errors.length > 0}
							<div class="message message-error">
								<p class="message-title">Lint errors</p>
								<ul>
									{#each createResult.lint.errors as err}<li>{err}</li>{/each}
								</ul>
							</div>
						{/if}
						{#if createResult.lint.warnings.length > 0}
							<div class="message message-info">
								<p class="message-title">Lint warnings</p>
								<ul>
									{#each createResult.lint.warnings as warn}<li>{warn}</li>{/each}
								</ul>
							</div>
						{/if}
						{#if createResult.lint.errors.length === 0 && createResult.lint.warnings.length === 0}
							<p class="message message-success">Lint clean - no issues found.</p>
						{/if}
					</div>
				{/if}
			{/if}
		</div>

		<div class="footer">
			{#if createResult}
				<button type="button" class="btn btn-secondary" onclick={onClose}>Close</button>
				<button type="button" class="btn btn-primary" onclick={openInPresets} data-import-open-preset>Open in Presets</button>
			{:else if !reviewing}
				<button type="button" class="btn btn-secondary" onclick={onClose}>Cancel</button>
				<button
					type="button"
					class="btn btn-primary"
					disabled={analyzing || !rawText.trim()}
					onclick={runAnalyze}
					data-import-analyze
				>
					{analyzing ? 'Analyzing…' : 'Analyze'}
				</button>
			{:else}
				<button type="button" class="btn btn-secondary" disabled={creating} onclick={onClose}>Cancel</button>
				<button type="button" class="btn btn-primary" disabled={!canCreate} onclick={runCreate} data-import-create>
					{creating ? 'Creating…' : 'Create preset'}
				</button>
			{/if}
		</div>
	</div>
</div>


<style>
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
		display: flex;
		flex-direction: column;
		width: min(760px, calc(100vw - 32px));
		max-height: 90vh;
		background: rgb(var(--surface-1, 22 24 28));
		border-radius: 10px;
		box-shadow: 0 16px 48px rgb(0 0 0 / 0.6);
	}

	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-shrink: 0;
		padding: 16px 24px;
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
	}

	.header-title {
		display: flex;
		align-items: center;
		gap: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
	}

	.header-title h2 {
		margin: 0;
		color: rgb(var(--fg, 232 234 237));
		font-size: 16px;
		font-weight: 600;
	}

	.close-btn {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		padding: 0;
		color: rgb(var(--fg-muted, 169 174 184));
		background: transparent;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}

	.close-btn:hover:not(:disabled) {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-2, 31 33 38));
	}

	.close-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.body {
		display: flex;
		flex-direction: column;
		gap: 16px;
		padding: 24px;
		overflow-y: auto;
	}

	.hint {
		margin: 0;
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 12px;
		line-height: 1.5;
	}

	.dropzone {
		display: flex;
		flex-direction: column;
		border: 1px dashed rgb(var(--line-strong, 43 46 53));
		border-radius: 6px;
		background: rgb(var(--surface-2, 31 33 38) / 0.4);
		transition: border-color 0.1s ease, background-color 0.1s ease;
	}

	.dropzone.dragover {
		border-color: rgb(var(--signal, 91 157 255));
		background: rgb(var(--signal, 91 157 255) / 0.06);
	}

	.dropzone textarea {
		width: 100%;
		box-sizing: border-box;
		padding: 10px 12px;
		color: rgb(var(--fg, 232 234 237));
		background: transparent;
		border: none;
		resize: vertical;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 12px;
		line-height: 1.6;
	}

	.dropzone textarea:focus {
		outline: none;
	}

	.dropzone-footer {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 8px 12px;
		border-top: 1px solid rgb(var(--line, 36 38 44));
		font-size: 12px;
	}

	.file-input-hidden {
		display: none;
	}

	.dim {
		color: rgb(var(--fg-subtle, 122 128 144));
	}

	.mono {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-variant-numeric: tabular-nums;
	}

	.link-btn {
		padding: 0;
		border: none;
		background: transparent;
		color: rgb(var(--signal, 91 157 255));
		font-size: 12px;
		cursor: pointer;
	}

	.link-btn:hover {
		text-decoration: underline;
	}

	.link-btn:disabled {
		opacity: 0.5;
		cursor: default;
		text-decoration: none;
	}

	.detected-strip {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		padding: 10px 12px;
		background: rgb(var(--signal, 91 157 255) / 0.06);
		border: 1px solid rgb(var(--signal, 91 157 255) / 0.2);
		border-radius: 6px;
		font-size: 12px;
	}

	.strip-end {
		margin-left: auto;
	}

	.badge {
		display: inline-flex;
		align-items: center;
		padding: 1px 7px;
		border-radius: 4px;
		background: rgb(var(--surface-3, 39 42 49));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.badge-info {
		background: rgb(var(--info, 91 157 255) / 0.15);
		color: rgb(var(--info, 91 157 255));
	}

	.candidate-list {
		display: flex;
		flex-direction: column;
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		overflow: hidden;
	}

	.candidate-row {
		display: grid;
		grid-template-columns: 20px minmax(0, 1.4fr) minmax(0, 1fr) 130px minmax(0, 1fr);
		align-items: center;
		gap: 10px;
		padding: 8px 12px;
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
		cursor: pointer;
	}

	.candidate-row:last-child {
		border-bottom: none;
	}

	.candidate-row:hover {
		background: rgb(var(--surface-2, 31 33 38) / 0.6);
	}

	.candidate-row input[type='checkbox'] {
		accent-color: rgb(var(--signal, 91 157 255));
	}

	.candidate-node {
		display: flex;
		flex-direction: column;
		gap: 1px;
		min-width: 0;
	}

	.candidate-title {
		color: rgb(var(--fg, 232 234 237));
		font-size: 12px;
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.candidate-input {
		color: rgb(var(--fg-subtle, 122 128 144));
		font-size: 11px;
	}

	.candidate-value {
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 11px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.candidate-type,
	.candidate-label {
		box-sizing: border-box;
		width: 100%;
		padding: 5px 8px;
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--field-bg, 31 33 38));
		border: 1px solid rgb(var(--field-border, 43 46 53));
		border-radius: 4px;
		font-size: 11px;
	}

	.candidate-type:focus,
	.candidate-label:focus {
		outline: none;
		border-color: rgb(var(--signal, 91 157 255));
	}

	.more-toggle {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 8px 12px;
		border: none;
		border-top: 1px solid rgb(var(--line, 36 38 44));
		background: rgb(var(--surface-2, 31 33 38) / 0.4);
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		cursor: pointer;
	}

	.more-toggle:hover {
		color: rgb(var(--fg, 232 234 237));
	}

	.chevron {
		transition: transform 0.1s ease;
	}

	.chevron.open {
		transform: rotate(90deg);
	}

	.name-grid {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 12px;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 0;
	}

	.field label {
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 11px;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.field input[type='text'] {
		box-sizing: border-box;
		width: 100%;
		padding: 8px 10px;
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--field-bg, 31 33 38));
		border: 1px solid rgb(var(--field-border, 43 46 53));
		border-radius: 4px;
		font-size: 12px;
		font-family: inherit;
	}

	.field input[type='text']:focus {
		outline: none;
		border-color: rgb(var(--signal, 91 157 255));
	}

	.lint-result {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.lint-heading {
		margin: 0;
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 12px;
	}

	.message {
		padding: 10px 14px;
		border-radius: 6px;
		font-size: 12px;
	}

	.message ul {
		margin: 4px 0 0;
		padding-left: 18px;
	}

	.message-error {
		margin: 0;
		color: rgb(var(--danger, 255 138 138));
		background: rgb(var(--danger, 255 138 138) / 0.1);
		border: 1px solid rgb(var(--danger, 255 138 138) / 0.25);
	}

	.message-info {
		color: rgb(var(--info, 91 157 255));
		background: rgb(var(--info, 91 157 255) / 0.1);
		border: 1px solid rgb(var(--info, 91 157 255) / 0.25);
	}

	.message-success {
		margin: 0;
		color: rgb(var(--success, 61 214 140));
		background: rgb(var(--success, 61 214 140) / 0.1);
		border: 1px solid rgb(var(--success, 61 214 140) / 0.25);
	}

	.message-title {
		margin: 0 0 4px;
		font-weight: 600;
	}

	.footer {
		display: flex;
		justify-content: flex-end;
		flex-shrink: 0;
		gap: 12px;
		padding: 16px 24px;
		border-top: 1px solid rgb(var(--line, 36 38 44));
	}

	.btn {
		padding: 8px 16px;
		border: none;
		border-radius: 4px;
		font-size: 13px;
		font-weight: 500;
		cursor: pointer;
	}

	.btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.btn-secondary {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-2, 31 33 38));
		border: 1px solid rgb(var(--line-strong, 43 46 53));
	}

	.btn-secondary:hover:not(:disabled) {
		background: rgb(var(--surface-3, 39 42 49));
	}

	.btn-primary {
		color: rgb(var(--accent-contrast, 22 22 22));
		background: rgb(var(--accent, 255 255 255));
	}

	.btn-primary:hover:not(:disabled) {
		background: rgb(var(--accent-hover, 230 230 230));
	}
</style>
