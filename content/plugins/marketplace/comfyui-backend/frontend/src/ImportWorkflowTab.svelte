<script>
	// Registered as this plugin's `admin_tabs[]` "Import workflow" tab (see
	// manifest.yml) - mounted by the core admin plugin-detail tab strip
	// (frontend/src/routes/admin/components/pluginDetailTabs.ts) with
	// `{ pluginId, plugin }` props, neither of which this component needs
	// (every endpoint below is this plugin's own, fixed path).
	//
	// A 4-step wizard replacing the old single-page modal (ImportWorkflowModal,
	// removed): Source -> Inputs -> Requirements -> Done, a numbered rail on
	// the left tracking progress, Back/Continue in a fixed footer. Requirements
	// checking is its own non-blocking step rather than something that used to
	// happen silently inside "Create preset". Plugin components can't import
	// core Svelte components or $lib - every control below reproduces the
	// Instrument tokens directly (see tokens.css), the same approach the old
	// modal used.
	import { onMount } from 'svelte';

	let { pluginId = 'comfyui-backend', plugin = null } = $props();

	const API_BASE = `/api/plugins/${pluginId}`;

	/** @type {1 | 2 | 3 | 4} */
	let step = $state(1);

	// ---- Step 1: Source ----
	let rawText = $state('');
	let dragOver = $state(false);
	let fileInputEl = $state(null);
	let analyzing = $state(false);
	let analyzeError = $state('');
	let analysis = $state(null);
	let workflowJson = $state(null);

	// ---- Step 2: Inputs ----
	let selectedKeys = $state(new Set());
	let fieldTypeByKey = $state({});
	let labelByKey = $state({});
	let moreOpen = $state(false);
	let fieldTypeOptions = $state([]);
	let families = $state([]);
	let modelFamily = $state('');
	let variant = $state('imported');
	let displayName = $state('');

	// ---- Step 3: Requirements ----
	let requirementsLoading = $state(false);
	let requirementsError = $state('');
	let requirementsResults = $state(null);

	// ---- Step 4: Done ----
	let creating = $state(false);
	let createError = $state('');
	let createResult = $state(null);

	let allRows = $derived(analysis ? groupRows(analysis.candidates) : []);
	let obviousRows = $derived(allRows.filter((r) => r.obvious));
	let moreRows = $derived(allRows.filter((r) => !r.obvious));
	let selectedRowCount = $derived(allRows.filter((r) => r.candidates.every((c) => selectedKeys.has(candidateKey(c)))).length);
	let canContinueInputs = $derived(!!modelFamily.trim() && !!displayName.trim());
	let requirementsOkCount = $derived(requirementsResults ? requirementsResults.filter((r) => r.status === 'ok').length : 0);
	let requirementsMissingCount = $derived(requirementsResults ? requirementsResults.filter((r) => r.status !== 'ok').length : 0);

	function candidateKey(c) {
		return `${c.node_id}:${c.input_name}`;
	}

	// A resolution candidate never appears alone (suggest.py's Resolution +
	// batch size loop always emits width and height together for the same
	// node) - shown as one row: one checkbox, one label, one field-type
	// select. The request to /presets/import still lists both underlying
	// {node_id, input_name} entries.
	function groupKeyForCandidate(c) {
		return c.suggested_field_type === 'resolution' ? `${c.node_id}:resolution` : candidateKey(c);
	}

	function groupRows(candidates) {
		const rows = [];
		const consumed = new Set();
		for (const c of candidates) {
			const key = candidateKey(c);
			if (consumed.has(key)) continue;
			if (c.suggested_field_type === 'resolution') {
				const partner = candidates.find(
					(o) =>
						o !== c &&
						o.node_id === c.node_id &&
						o.suggested_field_type === 'resolution' &&
						!consumed.has(candidateKey(o))
				);
				if (partner) {
					consumed.add(key);
					consumed.add(candidateKey(partner));
					const width = c.input_name === 'width' ? c : partner;
					const height = c.input_name === 'height' ? c : partner;
					rows.push({
						key: groupKeyForCandidate(c),
						candidates: [width, height],
						node_title: c.node_title,
						class_type: c.class_type,
						obvious: c.obvious,
						displayInput: 'width × height',
						displayValue: `${width.current_value} × ${height.current_value}`,
						suggestedFieldType: c.suggested_field_type,
						suggestedLabel: c.suggested_label
					});
					continue;
				}
			}
			consumed.add(key);
			rows.push({
				key: groupKeyForCandidate(c),
				candidates: [c],
				node_title: c.node_title,
				class_type: c.class_type,
				obvious: c.obvious,
				displayInput: c.input_name,
				displayValue: String(c.current_value),
				suggestedFieldType: c.suggested_field_type,
				suggestedLabel: c.suggested_label
			});
		}
		return rows;
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
				const res = await fetch(`${API_BASE}/presets/families`, { credentials: 'include', headers: authHeaders() });
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
			const res = await fetch(`${API_BASE}/presets/import/analyze`, {
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
			const rows = groupRows(payload.candidates);
			const nextSelected = new Set();
			const nextTypes = {};
			const nextLabels = {};
			for (const row of rows) {
				nextTypes[row.key] = row.suggestedFieldType;
				nextLabels[row.key] = row.suggestedLabel;
				if (row.obvious) {
					for (const c of row.candidates) nextSelected.add(candidateKey(c));
				}
			}
			selectedKeys = nextSelected;
			fieldTypeByKey = nextTypes;
			labelByKey = nextLabels;
			step = 2;
		} catch (e) {
			analyzeError = 'Could not reach the server.';
		} finally {
			analyzing = false;
		}
	}

	function changeWorkflow() {
		step = 1;
		analysis = null;
		workflowJson = null;
		requirementsResults = null;
		requirementsError = '';
		createResult = null;
		createError = '';
	}

	function toggleRow(row) {
		const allChecked = row.candidates.every((c) => selectedKeys.has(candidateKey(c)));
		const next = new Set(selectedKeys);
		for (const c of row.candidates) {
			if (allChecked) next.delete(candidateKey(c));
			else next.add(candidateKey(c));
		}
		selectedKeys = next;
	}

	async function goToRequirements() {
		step = 3;
		if (requirementsResults || requirementsLoading) return;
		await runRequirementsPreview();
	}

	async function runRequirementsPreview() {
		requirementsLoading = true;
		requirementsError = '';
		try {
			const res = await fetch(`${API_BASE}/presets/import/requirements`, {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json', ...authHeaders() },
				body: JSON.stringify({ workflow: workflowJson })
			});
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				requirementsError = payload?.detail || payload?.message || `Requirements check failed (${res.status})`;
				return;
			}
			requirementsResults = payload.results ?? [];
		} catch (e) {
			requirementsError = 'Could not reach the server.';
		} finally {
			requirementsLoading = false;
		}
	}

	function currentFields() {
		return analysis.candidates
			.filter((c) => selectedKeys.has(candidateKey(c)))
			.map((c) => {
				const key = groupKeyForCandidate(c);
				return {
					node_id: c.node_id,
					input_name: c.input_name,
					field_type: fieldTypeByKey[key] || c.suggested_field_type,
					label: labelByKey[key] || c.suggested_label
				};
			});
	}

	async function runCreate() {
		if (!analysis || !workflowJson || creating) return;
		createError = '';
		creating = true;
		try {
			const res = await fetch(`${API_BASE}/presets/import`, {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json', ...authHeaders() },
				body: JSON.stringify({
					workflow: workflowJson,
					fields: currentFields(),
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
			step = 4;
		} catch (e) {
			createError = 'Could not reach the server.';
		} finally {
			creating = false;
		}
	}

	function goBack() {
		if (step > 1) step -= 1;
	}

	function importAnother() {
		step = 1;
		rawText = '';
		analyzeError = '';
		analysis = null;
		workflowJson = null;
		selectedKeys = new Set();
		fieldTypeByKey = {};
		labelByKey = {};
		moreOpen = false;
		modelFamily = '';
		variant = 'imported';
		displayName = '';
		requirementsLoading = false;
		requirementsError = '';
		requirementsResults = null;
		creating = false;
		createError = '';
		createResult = null;
	}

	function stepState(index) {
		if (index < step) return 'done';
		if (index === step) return 'current';
		return 'upcoming';
	}
</script>

{#snippet stepDot(index)}
	{#if stepState(index) === 'done'}
		<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
			<polyline points="20 6 9 17 4 12" />
		</svg>
	{:else}
		{index}
	{/if}
{/snippet}

{#snippet candidateRow(row)}
	{@const key = row.key}
	{@const checked = row.candidates.every((c) => selectedKeys.has(candidateKey(c)))}
	<label class="candidate-row" for={`import-cb-${key}`}>
		<input id={`import-cb-${key}`} type="checkbox" {checked} onchange={() => toggleRow(row)} />
		<div class="candidate-node">
			<span class="candidate-title">{row.node_title || row.class_type}</span>
			<span class="candidate-input mono">{row.displayInput}</span>
		</div>
		<span class="candidate-value mono" title={row.displayValue}>{row.displayValue}</span>
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

<div class="wizard" data-import-wizard>
	<div class="wiz-rail">
		<div class="wiz-step {stepState(1)}" data-wiz-step="source">
			<div class="wiz-num">{@render stepDot(1)}</div>
			<div class="wiz-text">
				<div class="wiz-label">Source</div>
				{#if analysis}
					<div class="wiz-sub mono">{analysis.format === 'ui' ? 'export (ui)' : 'export (api)'} · {analysis.node_count} nodes</div>
				{/if}
			</div>
		</div>
		<div class="wiz-step {stepState(2)}" data-wiz-step="inputs">
			<div class="wiz-num">{@render stepDot(2)}</div>
			<div class="wiz-text">
				<div class="wiz-label">Inputs</div>
				<div class="wiz-sub">{step > 2 ? `${selectedRowCount} field${selectedRowCount === 1 ? '' : 's'} exposed` : 'choose exposed fields'}</div>
			</div>
		</div>
		<div class="wiz-step {stepState(3)}" data-wiz-step="requirements">
			<div class="wiz-num">{@render stepDot(3)}</div>
			<div class="wiz-text">
				<div class="wiz-label">Requirements</div>
				{#if requirementsLoading}
					<div class="wiz-sub">checking…</div>
				{:else if requirementsResults}
					<div class="wiz-sub mono">{requirementsOkCount} ok · {requirementsMissingCount} missing</div>
				{/if}
			</div>
		</div>
		<div class="wiz-step {stepState(4)}" data-wiz-step="done">
			<div class="wiz-num">{@render stepDot(4)}</div>
			<div class="wiz-text"><div class="wiz-label">Done</div></div>
		</div>
	</div>

	<div class="wiz-body">
		<div class="wiz-content">
			{#if step === 1}
				<h3>Choose a workflow</h3>
				<p class="desc">Paste or drop a ComfyUI workflow — the plain workflow JSON or Export (API).</p>

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
			{:else if step === 2}
				<h3>Choose the fields to expose</h3>
				<p class="desc">These become the preset's form. Everything else keeps the value baked into the workflow.</p>

				<div class="detected-strip" data-import-detected>
					<span class="chip chip-info" data-import-format>{analysis.format}</span>
					<span class="dim">·</span>
					<span class="mono">{analysis.node_count} nodes</span>
					{#if analysis.object_info_used}
						<span class="dim">·</span>
						<span class="dim" data-import-object-info-used>ranges + options from your ComfyUI</span>
					{/if}
					{#if analysis.lora_chain}
						<span class="dim">·</span>
						<span class="chip chip-info">LoRA chain found</span>
					{/if}
					<button type="button" class="link-btn strip-end" onclick={changeWorkflow}>Change workflow</button>
				</div>

				<div class="candidate-list" data-import-candidates>
					{#each obviousRows as row (row.key)}
						{@render candidateRow(row)}
					{/each}

					{#if moreRows.length > 0}
						<button type="button" class="more-toggle" aria-expanded={moreOpen} onclick={() => (moreOpen = !moreOpen)}>
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
							More inputs ({moreRows.length})
						</button>
						{#if moreOpen}
							{#each moreRows as row (row.key)}
								{@render candidateRow(row)}
							{/each}
						{/if}
					{/if}
				</div>

				<div class="name-grid">
					<div class="field">
						<label for="import-model-family">Model family</label>
						<input id="import-model-family" type="text" list="import-model-family-list" bind:value={modelFamily} placeholder="e.g. SDXL" />
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
			{:else if step === 3}
				<h3>Requirements</h3>
				<p class="desc">What this preset will need to run, detected from the workflow's nodes and models.</p>

				<div class="well">
					<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:rgb(var(--info, 91 157 255));margin-top:1px" aria-hidden="true">
						<circle cx="12" cy="12" r="10" />
						<line x1="12" y1="11" x2="12" y2="16.5" />
						<circle cx="12" cy="7.5" r="0.75" fill="currentColor" stroke="none" />
					</svg>
					<span>You can still create the preset — it won't run until these are installed.</span>
				</div>

				{#if requirementsLoading}
					<div class="req-loading"><span class="spinner" aria-hidden="true"></span>Checking requirements…</div>
				{:else if requirementsError}
					<p class="message message-error">{requirementsError}</p>
				{:else if requirementsResults}
					{#if requirementsResults.length === 0}
						<div class="req-empty">This workflow needs nothing beyond ComfyUI's own built-in nodes.</div>
					{:else}
						<div class="req-list" data-import-requirements>
							{#each requirementsResults as r}
								<div class="req-row">
									<div class="req-dot {r.status === 'ok' ? 'ok' : 'missing'}"></div>
									<div>
										<div class="req-name mono">{r.type}: {r.name}</div>
										<div class="req-detail">{r.detail}</div>
										{#if r.status !== 'ok' && r.hint}
											<div class="req-hint">{r.hint}</div>
										{/if}
									</div>
								</div>
							{/each}
						</div>
					{/if}
				{/if}

				{#if createError}
					<p class="message message-error" data-import-create-error>{createError}</p>
				{/if}
			{:else if step === 4 && createResult}
				<h3>Preset created</h3>
				<p class="desc">
					It's ready in Presets{requirementsMissingCount > 0
						? ` — the ${requirementsMissingCount} missing requirement${requirementsMissingCount === 1 ? '' : 's'} from the last step won't block it from opening, only from running.`
						: '.'}
				</p>
				<div class="lint-block" data-import-lint>
					<p class="lint-path">Preset created at <span class="mono">{createResult.path}</span></p>
					{#if createResult.lint.errors.length > 0}
						<div class="message message-error">
							<p class="message-title">Lint errors</p>
							<ul>{#each createResult.lint.errors as err}<li>{err}</li>{/each}</ul>
						</div>
					{/if}
					{#if createResult.lint.warnings.length > 0}
						{#each createResult.lint.warnings as warn}
							<div class="lint-warn">
								<div class="lint-warn-title">Lint warnings</div>
								<div class="lint-warn-body">{warn}</div>
							</div>
						{/each}
					{/if}
					{#if createResult.lint.errors.length === 0 && createResult.lint.warnings.length === 0}
						<p class="message message-success">Lint clean - no issues found.</p>
					{/if}
				</div>
			{/if}
		</div>

		<div class="wiz-footer">
			{#if step === 4}
				<button type="button" class="btn btn-secondary" onclick={importAnother}>Import another</button>
				<div class="footer-spacer"></div>
				<a class="btn btn-primary" href={`/admin?tab=presets&preset=${createResult?.preset_id ?? ''}`} data-import-open-preset>Open in Presets</a>
			{:else}
				{#if step > 1}
					<button type="button" class="btn btn-secondary" disabled={analyzing || creating} onclick={goBack}>Back</button>
				{/if}
				<div class="footer-spacer"></div>
				{#if step === 1}
					<button type="button" class="btn btn-primary" disabled={analyzing || !rawText.trim()} onclick={runAnalyze} data-import-analyze>
						{analyzing ? 'Analyzing…' : 'Continue'}
					</button>
				{:else if step === 2}
					<button type="button" class="btn btn-primary" disabled={!canContinueInputs} onclick={goToRequirements} data-import-continue-inputs>
						Continue
					</button>
				{:else if step === 3}
					<button type="button" class="btn btn-primary" disabled={creating} onclick={runCreate} data-import-create>
						{creating ? 'Creating…' : 'Continue'}
					</button>
				{/if}
			{/if}
		</div>
	</div>
</div>

<style>
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

	.wizard {
		display: flex;
		width: 100%;
		max-width: none;
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--surface-1, 22 24 28));
		min-height: 480px;
	}
	.wiz-rail {
		width: 190px;
		flex-shrink: 0;
		border-right: 1px solid rgb(var(--line, 36 38 44));
		padding: 24px 18px;
	}
	.wiz-step {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		position: relative;
		padding-bottom: 30px;
	}
	.wiz-step:last-child {
		padding-bottom: 0;
	}
	.wiz-step:not(:last-child)::after {
		content: '';
		position: absolute;
		left: 10px;
		top: 24px;
		bottom: 4px;
		width: 1px;
		background: rgb(var(--line-strong, 43 46 53));
	}
	.wiz-num {
		width: 21px;
		height: 21px;
		border-radius: 50%;
		flex-shrink: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 10.5px;
		font-weight: 700;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}
	.wiz-step.done .wiz-num {
		background: rgb(var(--success, 61 214 140));
		color: rgb(var(--canvas, 12 13 15));
	}
	.wiz-step.current .wiz-num {
		border: 2px solid rgb(var(--signal, 91 157 255));
		color: rgb(var(--signal, 91 157 255));
	}
	.wiz-step.upcoming .wiz-num {
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.wiz-text {
		padding-top: 1px;
		min-width: 0;
	}
	.wiz-label {
		font-size: 12.5px;
		font-weight: 600;
		color: rgb(var(--fg, 232 234 237));
	}
	.wiz-step.upcoming .wiz-label {
		color: rgb(var(--fg-subtle, 122 128 144));
		font-weight: 500;
	}
	.wiz-sub {
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin-top: 2px;
	}

	.wiz-body {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
	}
	.wiz-content {
		flex: 1;
		padding: 24px 28px;
		overflow-y: auto;
	}
	.wiz-content h3 {
		font-size: 15px;
		font-weight: 600;
		margin: 0 0 4px;
		color: rgb(var(--fg, 232 234 237));
	}
	.wiz-content .desc {
		font-size: 12px;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin: 0 0 16px;
	}
	.wiz-footer {
		border-top: 1px solid rgb(var(--line, 36 38 44));
		padding: 12px 20px;
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.footer-spacer {
		flex: 1;
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

	.detected-strip {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		padding: 8px 12px;
		border-radius: 6px;
		background: rgb(var(--canvas, 12 13 15));
		border: 1px solid rgb(var(--line, 36 38 44));
		margin-bottom: 14px;
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
	}
	.strip-end {
		margin-left: auto;
	}
	.chip {
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 3px;
		display: inline-flex;
		align-items: center;
		gap: 4px;
		white-space: nowrap;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}
	.chip-info {
		background: rgb(var(--info, 91 157 255) / 0.13);
		color: rgb(var(--info, 91 157 255));
	}

	.candidate-list {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		overflow: hidden;
		margin-bottom: 16px;
	}
	.candidate-row {
		display: grid;
		grid-template-columns: 20px minmax(0, 1.4fr) minmax(0, 1fr) 110px minmax(0, 1fr);
		align-items: center;
		gap: 10px;
		padding: 9px 12px;
		cursor: pointer;
	}
	.candidate-row + .candidate-row {
		border-top: 1px solid rgb(var(--line, 36 38 44));
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
		font-size: 12.5px;
		font-weight: 500;
		color: rgb(var(--fg, 232 234 237));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.candidate-input {
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.candidate-value {
		font-size: 11.5px;
		color: rgb(var(--fg-muted, 169 174 184));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.candidate-type,
	.candidate-label {
		box-sizing: border-box;
		width: 100%;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11.5px;
		padding: 0 8px;
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
		padding: 9px 12px;
		font-size: 11.5px;
		color: rgb(var(--fg-muted, 169 174 184));
		border: none;
		border-top: 1px solid rgb(var(--line, 36 38 44));
		width: 100%;
		background: rgb(var(--canvas, 12 13 15));
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
	.field label {
		display: block;
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin-bottom: 5px;
	}
	.field input[type='text'] {
		box-sizing: border-box;
		width: 100%;
		height: 30px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 12.5px;
		padding: 0 9px;
		font-family: inherit;
	}
	.field input[type='text']:focus {
		outline: none;
		border-color: rgb(var(--signal, 91 157 255));
	}

	.well {
		background: rgb(var(--surface-2, 31 33 38));
		border-radius: 6px;
		padding: 12px 14px;
		display: flex;
		gap: 10px;
		align-items: flex-start;
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
		margin-bottom: 14px;
	}

	.req-list {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--canvas, 12 13 15));
		overflow: hidden;
	}
	.req-row {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 10px 12px;
	}
	.req-row + .req-row {
		border-top: 1px solid rgb(var(--line, 36 38 44));
	}
	.req-dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		margin-top: 5px;
		flex-shrink: 0;
	}
	.req-dot.ok {
		background: rgb(var(--success, 61 214 140));
	}
	.req-dot.missing {
		background: rgb(var(--danger, 255 138 138));
	}
	.req-name {
		font-size: 12.5px;
		font-weight: 600;
		color: rgb(var(--fg, 232 234 237));
	}
	.req-detail {
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
	}
	.req-hint {
		font-size: 11.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin-top: 2px;
	}
	.req-empty {
		padding: 12px 14px;
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		color: rgb(var(--fg-subtle, 122 128 144));
		font-size: 12px;
	}
	.req-loading {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 0;
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

	.lint-block {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--canvas, 12 13 15));
		padding: 14px 16px;
	}
	.lint-path {
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
		margin: 0 0 12px;
	}
	.lint-warn {
		border-left: 2px solid rgb(var(--warning, 255 197 61));
		padding-left: 10px;
		margin-bottom: 8px;
	}
	.lint-warn:last-child {
		margin-bottom: 0;
	}
	.lint-warn-title {
		font-size: 11.5px;
		font-weight: 600;
		color: rgb(var(--warning, 255 197 61));
		margin-bottom: 2px;
	}
	.lint-warn-body {
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
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
		margin: 0 0 12px;
		color: rgb(var(--danger, 255 138 138));
		background: rgb(var(--danger, 255 138 138) / 0.1);
		border: 1px solid rgb(var(--danger, 255 138 138) / 0.25);
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

	.btn {
		height: 30px;
		padding: 0 14px;
		border-radius: 4px;
		font-size: 12.5px;
		font-weight: 600;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		border: 1px solid transparent;
		cursor: pointer;
		white-space: nowrap;
		font-family: inherit;
		text-decoration: none;
	}
	.btn[disabled] {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.btn-primary {
		background: rgb(var(--accent, 255 255 255));
		color: rgb(var(--accent-contrast, 22 22 22));
	}
	.btn-primary:hover:not([disabled]) {
		background: rgb(var(--accent-hover, 230 230 230));
	}
	.btn-secondary {
		background: rgb(var(--surface-1, 22 24 28));
		border-color: rgb(var(--line-strong, 43 46 53));
		color: rgb(var(--fg-muted, 169 174 184));
	}
	.btn-secondary:hover:not([disabled]) {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-2, 31 33 38));
	}
</style>
