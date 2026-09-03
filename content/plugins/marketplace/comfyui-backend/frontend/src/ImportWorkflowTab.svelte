<script>
	// Registered as this plugin's `admin_tabs[]` "Import workflow" tab (see
	// manifest.yml) - mounted by the core admin plugin-detail tab strip
	// (frontend/src/routes/admin/components/pluginDetailTabs.ts) with
	// `{ pluginId, plugin }` props, neither of which this component needs
	// (every endpoint below is this plugin's own, fixed path).
	//
	// A 5-step wizard: Source -> Form -> History -> Requirements -> Done, a
	// numbered rail on the left tracking progress, Back/Continue in a fixed
	// footer. Form is a two-pane designer (workflow inputs on the left,
	// user-arranged tabs/items on the right); History is a table of the
	// designed fields controlling what a generation's history card records,
	// with a live preview. Plugin components can't import core Svelte
	// components or $lib - every control below reproduces the Instrument
	// tokens directly (see tokens.css).
	//
	// Edit entry point: the "Imported presets" tab's edit action (a
	// SEPARATELY mounted plugin dist bundle) stashes a preset id under this
	// same sessionStorage key and asks the host to switch here - see that
	// component's own header comment for why sessionStorage/a window event
	// rather than shared module state.
	import { onMount } from 'svelte';

	let { pluginId = 'comfyui-backend', plugin = null } = $props();

	const API_BASE = `/api/plugins/${pluginId}`;
	const EDIT_STORAGE_KEY = 'comfyui-import-edit-preset-id';

	const LOCKED_ROLES = new Set(['seed', 'prompt_positive', 'prompt_negative', 'batch_size']);

	const TRANSFORM_OPTIONS = [
		{ value: 'none', label: 'None' },
		{ value: 'strip_model_prefix', label: 'Strip model prefix' },
		{ value: 'split_wh_width', label: 'Split W×H → width' },
		{ value: 'split_wh_height', label: 'Split W×H → height' },
		{ value: 'seed', label: 'Seed' }
	];

	const FORMAT_OPTIONS = [
		{ value: 'as_is', label: 'As-is' },
		{ value: 'number', label: 'Number' },
		{ value: 'wxh', label: 'W × H' },
		{ value: 'model_name', label: 'Model name' },
		{ value: 'list', label: 'List (count + names)' },
		{ value: 'jinja', label: 'Custom Jinja' }
	];

	/** @type {1 | 2 | 3 | 4 | 5} */
	let step = $state(1);

	// ---- Editing an existing imported preset (null = a brand-new import) ----
	let editPresetId = $state(null);
	let editLoading = $state(false);

	// ---- Step 1: Source ----
	let rawText = $state('');
	let dragOver = $state(false);
	let fileInputEl = $state(null);
	let analyzing = $state(false);
	let analyzeError = $state('');
	let analysis = $state(null);
	let workflowJson = $state(null);

	// ---- Step 2: Form ----
	let _uidCounter = 0;
	function uid(prefix) {
		_uidCounter += 1;
		return `${prefix}_${_uidCounter}`;
	}

	function emptyForm() {
		return { tabs: [{ id: 'generation', label: 'Generation', icon: null, items: [] }] };
	}

	let form = $state(emptyForm());
	let activeTabId = $state('generation');
	let fieldTypeOptions = $state([]);
	let families = $state([]);
	let modelFamily = $state('');
	let variant = $state('imported');
	let displayName = $state('');
	let leftSearch = $state('');
	let renamingTabId = $state(null);
	let tabPopoverId = $state(null);
	let addMenuOpenFor = $state(null);
	let expandedFieldId = $state(null);

	// ---- Step 3: History ----
	let initialHistoryDefault = $state([]);
	let historyRows = $state([]);
	let historyBuilt = $state(false);

	// ---- Step 4: Requirements ----
	let requirementsLoading = $state(false);
	let requirementsError = $state('');
	let requirementsResults = $state(null);

	// ---- Step 5: Done ----
	let creating = $state(false);
	let createError = $state('');
	let createResult = $state(null);

	let activeTab = $derived(form.tabs.find((t) => t.id === activeTabId) || form.tabs[0]);
	let allFields = $derived(collectFields(form));
	let mappedKeySet = $derived(new Set(allFields.flatMap((f) => f.mappings.map((m) => `${m.node_id}:${m.input_name}`))));
	let mappedFieldByKey = $derived(
		new Map(allFields.flatMap((f) => f.mappings.map((m) => [`${m.node_id}:${m.input_name}`, f])))
	);
	let leftGroups = $derived(buildLeftGroups(analysis?.candidates || [], leftSearch));
	let mappableCandidates = $derived((analysis?.candidates || []).filter((c) => !isLockedCandidate(c)));
	let formFieldCount = $derived(allFields.length);
	let canContinueForm = $derived(!!modelFamily.trim() && !!displayName.trim());
	let enabledHistoryCount = $derived(historyRows.filter((r) => r.enabled).length);
	let offHistoryCount = $derived(historyRows.length - enabledHistoryCount);
	let requirementsOkCount = $derived(requirementsResults ? requirementsResults.filter((r) => r.status === 'ok').length : 0);
	let requirementsMissingCount = $derived(
		requirementsResults ? requirementsResults.filter((r) => r.status !== 'ok').length : 0
	);

	function candidateKey(c) {
		return `${c.node_id}:${c.input_name}`;
	}

	function isLockedCandidate(c) {
		return LOCKED_ROLES.has(c.role);
	}

	function buildLeftGroups(candidates, search) {
		const q = search.trim().toLowerCase();
		const byNode = new Map();
		for (const c of candidates) {
			if (q && !`${c.node_title || ''} ${c.class_type || ''} ${c.input_name}`.toLowerCase().includes(q)) continue;
			if (!byNode.has(c.node_id)) {
				byNode.set(c.node_id, { node_id: c.node_id, node_title: c.node_title || c.class_type, class_type: c.class_type, rows: [] });
			}
			byNode.get(c.node_id).rows.push(c);
		}
		return [...byNode.values()];
	}

	// ---- Form tree: hydrate (attach local _id) / dehydrate (strip it) ----
	// A "resolution" field's `default` is a plain "<width>x<height>" string on
	// the wire (emit.py's split_wh_* transform splits it on literal "x") -
	// `_wh` is a local-only shadow of its two dimensions for the edit UI,
	// never sent back (dehydrateItem only copies the known field keys).
	function parseWh(value) {
		const m = typeof value === 'string' ? value.match(/^(\d+)\D+(\d+)$/) : null;
		return m ? { width: Number(m[1]), height: Number(m[2]) } : null;
	}

	function whToDefault(wh) {
		return `${wh.width ?? 0}x${wh.height ?? 0}`;
	}

	function hydrateItem(it) {
		const _id = uid('item');
		if (it.kind === 'row') return { _id, kind: 'row', columns: it.columns || 2, items: (it.items || []).map(hydrateItem) };
		if (it.kind === 'group') return { _id, kind: 'group', title: it.title || 'Group', items: (it.items || []).map(hydrateItem) };
		if (it.kind === 'section')
			return { _id, kind: 'section', title: it.title || 'Section', collapsed: !!it.collapsed, items: (it.items || []).map(hydrateItem) };
		if (it.kind === 'header') return { _id, kind: 'header', text: it.text || '' };
		const field = {
			_id,
			kind: 'field',
			field_name: it.field_name,
			field_type: it.field_type || 'text',
			label: it.label || it.field_name,
			default: it.default ?? null,
			config: it.config ?? null,
			mappings: (it.mappings || []).map((m) => ({ ...m }))
		};
		if (field.field_type === 'resolution') {
			const wh = parseWh(field.default);
			if (wh) field._wh = wh;
		}
		return field;
	}

	function hydrateTab(t) {
		return { id: t.id || uid('tab'), label: t.label || 'Tab', icon: t.icon ?? null, items: (t.items || []).map(hydrateItem) };
	}

	function hydrateForm(raw) {
		const tabs = raw?.tabs?.length ? raw.tabs.map(hydrateTab) : emptyForm().tabs;
		return { tabs };
	}

	function dehydrateItem(it) {
		if (it.kind === 'row') return { kind: 'row', columns: it.columns, items: it.items.map(dehydrateItem) };
		if (it.kind === 'group') return { kind: 'group', title: it.title, items: it.items.map(dehydrateItem) };
		if (it.kind === 'section') return { kind: 'section', title: it.title, collapsed: it.collapsed, items: it.items.map(dehydrateItem) };
		if (it.kind === 'header') return { kind: 'header', text: it.text };
		return {
			kind: 'field',
			field_name: it.field_name,
			field_type: it.field_type,
			label: it.label,
			default: it.default ?? null,
			config: it.config ?? null,
			mappings: it.mappings.map((m) => ({ node_id: m.node_id, input_name: m.input_name, transform: m.transform || 'none' }))
		};
	}

	function dehydrateForm() {
		return { tabs: form.tabs.map((t) => ({ id: t.id, label: t.label, icon: t.icon ?? null, items: t.items.map(dehydrateItem) })) };
	}

	function collectFieldsFromItems(items, acc) {
		for (const it of items) {
			if (it.kind === 'field') acc.push(it);
			else if (it.items) collectFieldsFromItems(it.items, acc);
		}
		return acc;
	}

	function collectFields(f) {
		const acc = [];
		for (const t of f.tabs) collectFieldsFromItems(t.items, acc);
		return acc;
	}

	function initializeFormAndHistory(payload) {
		form = hydrateForm(payload.form || payload.default_form || emptyForm());
		activeTabId = form.tabs[0]?.id || 'generation';
		initialHistoryDefault = payload.history || payload.default_history || [];
		historyRows = [];
		historyBuilt = false;
	}

	function uniqueFieldName(base) {
		let name = base || 'field';
		let n = 1;
		while (allFields.some((f) => f.field_name === name)) {
			n += 1;
			name = `${base}_${n}`;
		}
		return name;
	}

	function findFieldByName(name) {
		return allFields.find((f) => f.field_name === name) || null;
	}

	function defaultTransformFor(c) {
		if (c.suggested_field_type === 'resolution') return c.input_name === 'width' ? 'split_wh_width' : c.input_name === 'height' ? 'split_wh_height' : 'none';
		if (c.role === 'seed') return 'seed';
		return 'none';
	}

	function addCandidateToForm(c) {
		const key = candidateKey(c);
		if (mappedKeySet.has(key)) return;
		const name = c.suggested_field_name || c.input_name;
		const existing = findFieldByName(name);
		if (existing) {
			existing.mappings.push({ node_id: c.node_id, input_name: c.input_name, transform: defaultTransformFor(c) });
			if (c.suggested_field_type === 'resolution') {
				const wh = { ...(existing._wh || {}), [c.input_name]: c.current_value };
				existing._wh = wh;
				existing.default = whToDefault(wh);
			}
			return;
		}
		const tab = form.tabs.find((t) => t.id === activeTabId) || form.tabs[0];
		if (!tab) return;
		const field = {
			_id: uid('item'),
			kind: 'field',
			field_name: uniqueFieldName(name),
			field_type: c.suggested_field_type || 'text',
			label: c.suggested_label || c.input_name,
			default: c.suggested_field_type === 'resolution' ? whToDefault({ [c.input_name]: c.current_value }) : (c.current_value ?? null),
			config: c.suggested_config ?? null,
			mappings: [{ node_id: c.node_id, input_name: c.input_name, transform: defaultTransformFor(c) }]
		};
		if (c.suggested_field_type === 'resolution') field._wh = { [c.input_name]: c.current_value };
		tab.items.push(field);
	}

	function addTab() {
		let n = form.tabs.length + 1;
		let id = `tab_${n}`;
		while (form.tabs.some((t) => t.id === id)) {
			n += 1;
			id = `tab_${n}`;
		}
		form.tabs.push({ id, label: `Tab ${form.tabs.length + 1}`, icon: null, items: [] });
		activeTabId = id;
	}

	function moveTab(index, dir) {
		const j = index + dir;
		if (j < 0 || j >= form.tabs.length) return;
		const [t] = form.tabs.splice(index, 1);
		form.tabs.splice(j, 0, t);
	}

	function deleteTab(index) {
		if (form.tabs.length <= 1) return;
		const [removed] = form.tabs.splice(index, 1);
		if (activeTabId === removed.id) activeTabId = form.tabs[Math.max(0, index - 1)].id;
	}

	function moveItemAt(items, index, dir) {
		const j = index + dir;
		if (j < 0 || j >= items.length) return;
		const [it] = items.splice(index, 1);
		items.splice(j, 0, it);
	}

	function removeItemAt(items, index) {
		items.splice(index, 1);
	}

	function addItemToContainer(items, kind) {
		if (kind === 'field') items.push({ _id: uid('item'), kind: 'field', field_name: uniqueFieldName('field'), field_type: 'text', label: 'New field', default: null, config: null, mappings: [] });
		else if (kind === 'row') items.push({ _id: uid('item'), kind: 'row', columns: 2, items: [] });
		else if (kind === 'group') items.push({ _id: uid('item'), kind: 'group', title: 'Group', items: [] });
		else if (kind === 'section') items.push({ _id: uid('item'), kind: 'section', title: 'Section', collapsed: false, items: [] });
		else if (kind === 'header') items.push({ _id: uid('item'), kind: 'header', text: 'Header' });
		addMenuOpenFor = null;
	}

	function convertContainer(item) {
		if (item.kind === 'row') {
			item.kind = 'group';
			item.title = 'Group';
			delete item.columns;
		} else if (item.kind === 'group') {
			item.kind = 'row';
			item.columns = 2;
			delete item.title;
		}
	}

	function toggleMapping(field, c, checked) {
		const idx = field.mappings.findIndex((m) => m.node_id === c.node_id && m.input_name === c.input_name);
		if (checked && idx === -1) field.mappings.push({ node_id: c.node_id, input_name: c.input_name, transform: defaultTransformFor(c) });
		else if (!checked && idx !== -1) field.mappings.splice(idx, 1);
	}

	function setMappingTransform(field, c, value) {
		const m = field.mappings.find((m2) => m2.node_id === c.node_id && m2.input_name === c.input_name);
		if (m) m.transform = value;
	}

	function fieldTypeOptionsFor(current) {
		return [...new Set([current, ...fieldTypeOptions])].filter(Boolean);
	}

	function displayDefault(item) {
		if (item._wh) return `${item._wh.width ?? ''} × ${item._wh.height ?? ''}`;
		const d = item.default;
		if (Array.isArray(d)) return `${d.length} item${d.length === 1 ? '' : 's'}`;
		return d === null || d === undefined ? '' : String(d);
	}

	function setDefaultFromText(item, text) {
		if (item.field_type === 'resolution') {
			const m = text.match(/(-?\d+)\D+(-?\d+)/);
			if (m) {
				item._wh = { width: Number(m[1]), height: Number(m[2]) };
				item.default = whToDefault(item._wh);
			}
			return;
		}
		item.default = text;
	}

	// ---- History ----
	function buildHistoryRows(defaultHistory) {
		const byName = new Map((defaultHistory || []).map((h, i) => [h.field, { ...h, _order: i }]));
		const matched = [];
		const unmatched = [];
		for (const f of allFields) {
			const d = byName.get(f.field_name);
			if (d) matched.push({ field_name: f.field_name, label: d.label ?? f.label, format: d.format || 'as_is', template: d.template ?? null, enabled: true, _order: d._order });
			else unmatched.push({ field_name: f.field_name, label: f.label, format: 'as_is', template: null, enabled: false });
		}
		matched.sort((a, b) => a._order - b._order);
		historyRows = [...matched, ...unmatched].map(({ _order, ...r }) => r);
	}

	function syncHistoryRows() {
		const existingByName = new Map(historyRows.map((r) => [r.field_name, r]));
		const next = [];
		for (const f of allFields) {
			const prev = existingByName.get(f.field_name);
			next.push(prev || { field_name: f.field_name, label: f.label, format: 'as_is', template: null, enabled: false });
		}
		historyRows = next;
	}

	function goToHistory() {
		if (!historyBuilt) {
			buildHistoryRows(initialHistoryDefault);
			historyBuilt = true;
		} else {
			syncHistoryRows();
		}
		step = 3;
	}

	function fieldForRow(row) {
		return allFields.find((f) => f.field_name === row.field_name) || null;
	}

	function moveHistoryRow(index, dir) {
		const j = index + dir;
		if (j < 0 || j >= historyRows.length) return;
		const [r] = historyRows.splice(index, 1);
		historyRows.splice(j, 0, r);
	}

	function onFormatChange(row, value) {
		row.format = value;
		if (value === 'jinja' && !row.template) row.template = `{{ form.${row.field_name} }}`;
	}

	function formatPreviewValue(field, format, template) {
		const raw = field?.default;
		const wh = field?._wh || parseWh(raw);
		if (format === 'jinja') {
			const base = wh ? `${wh.width ?? ''} × ${wh.height ?? ''}` : String(raw ?? '');
			return (template || '').replace(/\{\{\s*form\.([a-zA-Z0-9_]+)\s*\}\}/g, (_m, name) => (field && name === field.field_name ? base : ''));
		}
		if (format === 'wxh') {
			if (wh) return `${wh.width ?? ''} × ${wh.height ?? ''}`;
			return String(raw ?? '');
		}
		if (format === 'model_name') {
			return String(raw ?? '').split('/').pop().split('\\').pop();
		}
		if (format === 'list') {
			if (Array.isArray(raw)) return `${raw.length} item${raw.length === 1 ? '' : 's'}`;
			return String(raw ?? '');
		}
		return String(raw ?? '');
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
				// Best-effort - the select still works with whatever the field suggests.
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

		let pendingEditId = null;
		try {
			pendingEditId = sessionStorage.getItem(EDIT_STORAGE_KEY);
			if (pendingEditId) sessionStorage.removeItem(EDIT_STORAGE_KEY);
		} catch (e) {
			// Private-window sessionStorage failure - just opens on a blank Source step.
		}
		if (pendingEditId) startEdit(pendingEditId);
	});

	async function startEdit(presetId) {
		editPresetId = presetId;
		editLoading = true;
		analyzeError = '';
		try {
			const res = await fetch(`${API_BASE}/presets/imported/${presetId}/source`, { credentials: 'include', headers: authHeaders() });
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				analyzeError = payload?.detail || payload?.message || `Could not load this preset (${res.status})`;
				editPresetId = null;
				return;
			}
			workflowJson = payload.workflow;
			analysis = payload;
			modelFamily = payload.model_family || '';
			variant = payload.variant || 'imported';
			displayName = payload.display_name || '';
			initializeFormAndHistory(payload);
			step = 1;
		} catch (e) {
			analyzeError = 'Could not reach the server.';
			editPresetId = null;
		} finally {
			editLoading = false;
		}
	}

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

	// Step 1's Continue: when a workflow is already analyzed (edit mode's
	// prefetched source, or simply returning to step 1 via Back without
	// touching "Change workflow"), just advance - no need to re-hit
	// /analyze for a workflow already in hand.
	function handleSourceContinue() {
		if (analysis && workflowJson) {
			step = 2;
			return;
		}
		runAnalyze();
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
			initializeFormAndHistory(payload);
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
		form = emptyForm();
		activeTabId = 'generation';
		historyRows = [];
		historyBuilt = false;
		initialHistoryDefault = [];
		requirementsResults = null;
		requirementsError = '';
		createResult = null;
		createError = '';
	}

	async function goToRequirementsStep() {
		step = 4;
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

	function historyPayload() {
		return historyRows
			.filter((r) => r.enabled)
			.map((r) => ({ field: r.field_name, label: r.label, format: r.format, template: r.format === 'jinja' ? r.template || '' : null }));
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
					model_family: modelFamily.trim(),
					variant: variant.trim() || 'imported',
					display_name: displayName.trim(),
					overwrite_preset_id: editPresetId || undefined,
					form: dehydrateForm(),
					history: historyPayload()
				})
			});
			const payload = await res.json().catch(() => null);
			if (!res.ok) {
				createError = payload?.detail || payload?.message || `Import failed (${res.status})`;
				return;
			}
			createResult = payload;
			step = 5;
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
		editPresetId = null;
		editLoading = false;
		rawText = '';
		analyzeError = '';
		analysis = null;
		workflowJson = null;
		form = emptyForm();
		activeTabId = 'generation';
		leftSearch = '';
		renamingTabId = null;
		tabPopoverId = null;
		addMenuOpenFor = null;
		expandedFieldId = null;
		modelFamily = '';
		variant = 'imported';
		displayName = '';
		historyRows = [];
		historyBuilt = false;
		initialHistoryDefault = [];
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

{#snippet icon(name, size = 12)}
	<svg class="icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"><use href={`#i-${name}`}></use></svg>
{/snippet}

{#snippet addMenu(items, isRoot = false)}
	<div class="di-add-wrap" data-add-root={isRoot ? 'true' : undefined}>
		<button type="button" class="di-add-field" onclick={() => (addMenuOpenFor = addMenuOpenFor === items ? null : items)} data-action="open-add-menu">
			{@render icon('plus')} Add
		</button>
		{#if addMenuOpenFor === items}
			<div class="di-add-menu">
				<button type="button" onclick={() => addItemToContainer(items, 'field')} data-add-kind="field">{@render icon('arrow-right')}Field<span class="type-tag">input</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'row')} data-add-kind="row">{@render icon('columns')}Row<span class="type-tag">layout</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'group')} data-add-kind="group">{@render icon('folder')}Group<span class="type-tag">layout</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'section')} data-add-kind="section">{@render icon('layers')}Section<span class="type-tag">accordion</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'header')} data-add-kind="header">{@render icon('heading')}Header<span class="type-tag">display</span></button>
			</div>
		{/if}
	</div>
{/snippet}

{#snippet fieldCard(item, parentItems, index)}
	<div class="di-field-card" data-field-name={item.field_name}>
		<div class="di-field-top">
			{@render icon('grip')}
			<input class="di-field-label" type="text" bind:value={item.label} aria-label="Field label" />
			<select class="di-field-type" value={item.field_type} onchange={(e) => (item.field_type = e.currentTarget.value)} aria-label="Field type">
				{#each fieldTypeOptionsFor(item.field_type) as opt}<option value={opt}>{opt}</option>{/each}
			</select>
			<input class="di-field-default" type="text" value={displayDefault(item)} oninput={(e) => setDefaultFromText(item, e.currentTarget.value)} aria-label="Default value" />
			<div class="di-field-actions">
				<button
					type="button"
					class="iconbtn"
					class:active={expandedFieldId === item._id}
					title="Edit mapping"
					aria-expanded={expandedFieldId === item._id}
					onclick={() => (expandedFieldId = expandedFieldId === item._id ? null : item._id)}
					data-action="toggle-mapping"
				>
					{@render icon(expandedFieldId === item._id ? 'chevron-up' : 'chevron-down')}
				</button>
				<button type="button" class="iconbtn" title="Move up" onclick={() => moveItemAt(parentItems, index, -1)} data-action="move-up">{@render icon('chevron-up')}</button>
				<button type="button" class="iconbtn" title="Move down" onclick={() => moveItemAt(parentItems, index, 1)} data-action="move-down">{@render icon('chevron-down')}</button>
				<button type="button" class="iconbtn" title="Remove" onclick={() => removeItemAt(parentItems, index)} data-action="remove">{@render icon('x')}</button>
			</div>
		</div>
		{#if item.mappings.length > 0}
			<div class="di-mapping"><span class="line mono"><span class="arrow">→</span>{item.mappings.map((m) => `${m.node_id}.inputs.${m.input_name}`).join(' · ')}</span></div>
		{/if}
		{#if expandedFieldId === item._id}
			<div class="di-mapedit" data-mapping-editor>
				<div class="di-mapedit-label">Mapped workflow inputs</div>
				<div class="di-mapedit-list">
					{#each mappableCandidates as c (candidateKey(c))}
						{@const checked = item.mappings.some((m) => m.node_id === c.node_id && m.input_name === c.input_name)}
						{@const mappedElsewhere = mappedFieldByKey.get(candidateKey(c)) && mappedFieldByKey.get(candidateKey(c)) !== item}
						<div class="di-mapedit-row" class:selected={checked} data-mapedit-key={candidateKey(c)}>
							<input type="checkbox" {checked} disabled={mappedElsewhere} onchange={(e) => toggleMapping(item, c, e.currentTarget.checked)} />
							<span class="di-mapedit-node mono">{c.node_id} · {c.class_type}</span>
							<span class="di-mapedit-input mono">{c.input_name}</span>
							{#if mappedElsewhere}
								<span class="di-mapedit-taken">mapped to {mappedFieldByKey.get(candidateKey(c)).label}</span>
							{:else if checked}
								<div class="di-mapedit-transform">
									<select value={item.mappings.find((m) => m.node_id === c.node_id && m.input_name === c.input_name)?.transform || 'none'} onchange={(e) => setMappingTransform(item, c, e.currentTarget.value)}>
										{#each TRANSFORM_OPTIONS as t}<option value={t.value}>{t.label}</option>{/each}
									</select>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/if}
	</div>
{/snippet}

{#snippet headerCard(item, parentItems, index)}
	<div class="di-header-card">
		{@render icon('grip')}
		<span class="chip chip-warn">HEADER</span>
		<input class="di-header-input" type="text" bind:value={item.text} aria-label="Header text" />
		<div class="di-container-actions">
			<button type="button" class="iconbtn" title="Move up" onclick={() => moveItemAt(parentItems, index, -1)} data-action="move-up">{@render icon('chevron-up')}</button>
			<button type="button" class="iconbtn" title="Move down" onclick={() => moveItemAt(parentItems, index, 1)} data-action="move-down">{@render icon('chevron-down')}</button>
			<button type="button" class="iconbtn" title="Remove" onclick={() => removeItemAt(parentItems, index)} data-action="remove">{@render icon('x')}</button>
		</div>
	</div>
{/snippet}

{#snippet anyItem(item, parentItems, index)}
	{#if item.kind === 'field'}
		{@render fieldCard(item, parentItems, index)}
	{:else if item.kind === 'header'}
		{@render headerCard(item, parentItems, index)}
	{:else if item.kind === 'row'}
		{@render containerCard(item, parentItems, index, 'ROW', 'chip-violet')}
	{:else if item.kind === 'group'}
		{@render containerCard(item, parentItems, index, 'GROUP', 'chip-mute')}
	{:else if item.kind === 'section'}
		{@render containerCard(item, parentItems, index, 'SECTION', 'chip-info')}
	{/if}
{/snippet}

{#snippet itemsList(items)}
	{#each items as it, index (it._id)}
		{@render anyItem(it, items, index)}
	{/each}
	{@render addMenu(items)}
{/snippet}

{#snippet containerCard(item, parentItems, index, chipLabel, chipClass)}
	<div class="di-container" class:collapsed={item.kind === 'section' && item.collapsed} data-item-kind={item.kind}>
		<div class="di-container-head">
			{@render icon('grip')}
			<span class="chip {chipClass}">{chipLabel}</span>
			{#if item.kind !== 'row'}
				<input class="di-container-title" type="text" bind:value={item.title} aria-label="Container title" />
			{/if}
			{#if item.kind === 'section'}
				<button type="button" class="iconbtn" onclick={() => (item.collapsed = !item.collapsed)} title="Toggle section" data-action="toggle-section">
					<span class="di-chevron" class:collapsed={item.collapsed}>{@render icon('chevron-down')}</span>
				</button>
			{/if}
			<div class="di-container-spacer"></div>
			{#if item.kind === 'row'}
				<div class="di-col-control">
					<span class="lbl">Columns</span>
					{#each [2, 3, 4] as n}
						<button type="button" class="di-col-btn" class:active={item.columns === n} onclick={() => (item.columns = n)} data-columns={n}>{n}</button>
					{/each}
				</div>
			{/if}
			<div class="di-container-actions">
				{#if item.kind === 'row' || item.kind === 'group'}
					<button type="button" class="iconbtn" title={item.kind === 'row' ? 'Convert to Group' : 'Convert to Row'} onclick={() => convertContainer(item)} data-action="convert">
						{@render icon('more')}
					</button>
				{/if}
				<button type="button" class="iconbtn" title="Move up" onclick={() => moveItemAt(parentItems, index, -1)} data-action="move-up">{@render icon('chevron-up')}</button>
				<button type="button" class="iconbtn" title="Move down" onclick={() => moveItemAt(parentItems, index, 1)} data-action="move-down">{@render icon('chevron-down')}</button>
				<button type="button" class="iconbtn" title="Remove" onclick={() => removeItemAt(parentItems, index)} data-action="remove">{@render icon('x')}</button>
			</div>
		</div>
		<div class="di-container-body">
			{#if item.kind === 'row'}
				<div class="di-row-cols" style={`grid-template-columns: repeat(${item.columns}, 1fr)`}>
					{#each item.items as child, ci (child._id)}
						{@render anyItem(child, item.items, ci)}
					{/each}
				</div>
				{@render addMenu(item.items)}
			{:else}
				{@render itemsList(item.items)}
			{/if}
		</div>
	</div>
{/snippet}

<svg style="display:none" aria-hidden="true">
	<defs>
		<symbol id="i-check" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-lock" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" fill="none" stroke="currentColor" stroke-width="2" /></symbol>
		<symbol id="i-arrow-right" viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><polyline points="12 5 19 12 12 19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-arrow-left" viewBox="0 0 24 24"><line x1="19" y1="12" x2="5" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><polyline points="12 19 5 12 12 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-grip" viewBox="0 0 24 24"><circle cx="9" cy="6" r="1.4" fill="currentColor" /><circle cx="9" cy="12" r="1.4" fill="currentColor" /><circle cx="9" cy="18" r="1.4" fill="currentColor" /><circle cx="15" cy="6" r="1.4" fill="currentColor" /><circle cx="15" cy="12" r="1.4" fill="currentColor" /><circle cx="15" cy="18" r="1.4" fill="currentColor" /></symbol>
		<symbol id="i-chevron-up" viewBox="0 0 24 24"><polyline points="18 15 12 9 6 15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-chevron-down" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-x" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></symbol>
		<symbol id="i-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6" fill="currentColor" /><circle cx="12" cy="12" r="1.6" fill="currentColor" /><circle cx="19" cy="12" r="1.6" fill="currentColor" /></symbol>
		<symbol id="i-plus" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></symbol>
		<symbol id="i-pencil" viewBox="0 0 24 24"><path d="M12 20h9" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-columns" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="16" rx="1" fill="none" stroke="currentColor" stroke-width="2" /><rect x="14" y="4" width="7" height="16" rx="1" fill="none" stroke="currentColor" stroke-width="2" /></symbol>
		<symbol id="i-folder" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></symbol>
		<symbol id="i-layers" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /><polyline points="2 17 12 22 22 17" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /><polyline points="2 12 12 17 22 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></symbol>
		<symbol id="i-heading" viewBox="0 0 24 24"><path d="M6 4v16M18 4v16M6 12h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></symbol>
		<symbol id="i-inbox" viewBox="0 0 24 24"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></symbol>
		<symbol id="i-sliders" viewBox="0 0 24 24"><line x1="4" y1="21" x2="4" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="4" y1="10" x2="4" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="12" y1="21" x2="12" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="12" y1="8" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="20" y1="21" x2="20" y2="16" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="20" y1="12" x2="20" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="1" y1="14" x2="7" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="9" y1="8" x2="15" y2="8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="17" y1="16" x2="23" y2="16" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></symbol>
	</defs>
</svg>

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
		<div class="wiz-step {stepState(2)}" data-wiz-step="form">
			<div class="wiz-num">{@render stepDot(2)}</div>
			<div class="wiz-text">
				<div class="wiz-label">Form</div>
				<div class="wiz-sub">{formFieldCount > 0 ? `${formFieldCount} field${formFieldCount === 1 ? '' : 's'} designed` : 'design the form'}</div>
			</div>
		</div>
		<div class="wiz-step {stepState(3)}" data-wiz-step="history">
			<div class="wiz-num">{@render stepDot(3)}</div>
			<div class="wiz-text">
				<div class="wiz-label">History</div>
				{#if historyBuilt}
					<div class="wiz-sub mono">{enabledHistoryCount} recorded{offHistoryCount > 0 ? ` · ${offHistoryCount} off` : ''}</div>
				{/if}
			</div>
		</div>
		<div class="wiz-step {stepState(4)}" data-wiz-step="requirements">
			<div class="wiz-num">{@render stepDot(4)}</div>
			<div class="wiz-text">
				<div class="wiz-label">Requirements</div>
				{#if requirementsLoading}
					<div class="wiz-sub">checking…</div>
				{:else if requirementsResults}
					<div class="wiz-sub mono">{requirementsOkCount} ok · {requirementsMissingCount} missing</div>
				{/if}
			</div>
		</div>
		<div class="wiz-step {stepState(5)}" data-wiz-step="done">
			<div class="wiz-num">{@render stepDot(5)}</div>
			<div class="wiz-text"><div class="wiz-label">Done</div></div>
		</div>
	</div>

	<div class="wiz-body">
		<div class="wiz-content">
			{#if editPresetId}
				<div class="edit-banner" data-import-editing>
					Editing <strong>{displayName || 'this preset'}</strong> — Continue updates it in place, it won't become a new preset.
				</div>
			{/if}
			{#if step === 1}
				<h3>Choose a workflow</h3>
				{#if editLoading}
					<div class="req-loading"><span class="spinner" aria-hidden="true"></span>Loading the preset's source workflow…</div>
				{:else if editPresetId && analysis}
					<p class="desc">This preset's stored workflow is already loaded - Continue to keep it, or load a different one.</p>
					<div class="detected-strip" data-import-detected>
						<span class="chip chip-info" data-import-format>{analysis.format}</span>
						<span class="dim">·</span>
						<span class="mono">{analysis.node_count} nodes</span>
						<span class="dim">·</span>
						<span>Loaded from the existing preset</span>
						<button type="button" class="link-btn strip-end" onclick={changeWorkflow}>Change workflow</button>
					</div>
					{#if analyzeError}
						<p class="message message-error" data-import-analyze-error>{analyzeError}</p>
					{/if}
				{:else}
					<p class="desc">Paste or drop a ComfyUI workflow — the plain workflow JSON or Export (API).</p>

					<div class="dropzone" class:dragover={dragOver} ondrop={handleDrop} ondragover={handleDragOver} ondragleave={handleDragLeave} role="group" aria-label="Workflow JSON">
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
							<input bind:this={fileInputEl} type="file" accept=".json,application/json" class="file-input-hidden" onchange={handleFileInput} />
						</div>
					</div>

					{#if analyzeError}
						<p class="message message-error" data-import-analyze-error>{analyzeError}</p>
					{/if}
				{/if}
			{:else if step === 2}
				<h3>Design the form</h3>
				<p class="desc">Pick which workflow inputs become fields, then arrange them into tabs. Anything left unmapped keeps the value baked into the workflow.</p>

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

				<div class="designer">
					<div class="di-left" data-import-form-inputs>
						<div class="di-left-title">Workflow inputs</div>
						<div class="di-search"><input type="text" placeholder="Search inputs…" bind:value={leftSearch} /></div>
						{#each leftGroups as g (g.node_id)}
							<div class="di-group-h">{g.node_title} <span class="cls mono">{g.class_type}</span></div>
							{#each g.rows as c (candidateKey(c))}
								{@const key = candidateKey(c)}
								{@const locked = isLockedCandidate(c)}
								{@const mappedField = mappedFieldByKey.get(key)}
								<div class="di-row" class:locked class:mapped={!!mappedField} data-input-key={key}>
									<span class="di-row-name mono">{c.input_name}</span>
									{#if locked}
										<span class="lock-badge">{@render icon('lock', 10)}always wired</span>
									{:else if mappedField}
										<span class="di-row-value">{mappedField.label}</span>
									{:else}
										<span class="di-row-value mono">{String(c.current_value)}</span>
										<span class="chip chip-info">{(c.value_type || c.suggested_field_type || '').toUpperCase()}</span>
									{/if}
									<div class="di-row-trail">
										{#if mappedField}
											{@render icon('check', 13)}
										{:else if !locked}
											<button type="button" class="iconbtn di-add-arrow" title="Add to form" onclick={() => addCandidateToForm(c)} data-action="add-input">
												{@render icon('arrow-right', 13)}
											</button>
										{/if}
									</div>
								</div>
							{/each}
						{/each}
					</div>

					<div class="di-right">
						<div class="di-tabs" data-import-form-tabs>
							{#each form.tabs as tab, ti (tab.id)}
								<span
									class="di-tab"
									class:active={tab.id === activeTabId}
									role="tab"
									tabindex="0"
									aria-selected={tab.id === activeTabId}
									onclick={() => (activeTabId = tab.id)}
									onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activeTabId = tab.id; } }}
									data-tab-id={tab.id}
								>
									{#if renamingTabId === tab.id}
										<input
											class="di-tab-rename"
											type="text"
											value={tab.label}
											onclick={(e) => e.stopPropagation()}
											onblur={(e) => {
												tab.label = e.currentTarget.value.trim() || tab.label;
												renamingTabId = null;
											}}
											onkeydown={(e) => {
												if (e.key === 'Enter') e.currentTarget.blur();
												if (e.key === 'Escape') renamingTabId = null;
											}}
										/>
									{:else}
										{tab.label}
									{/if}
									<span
										class="kb"
										role="button"
										tabindex="0"
										title="Tab options"
										onclick={(e) => { e.stopPropagation(); tabPopoverId = tabPopoverId === tab.id ? null : tab.id; }}
										onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); tabPopoverId = tabPopoverId === tab.id ? null : tab.id; } }}
										data-action="tab-menu"
									>
										{@render icon('more', 11)}
									</span>
									{#if tabPopoverId === tab.id}
										<div class="tab-popover" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>
											<button type="button" onclick={() => { renamingTabId = tab.id; tabPopoverId = null; }} data-action="rename-tab">{@render icon('pencil')}Rename</button>
											<button type="button" disabled={ti === 0} onclick={() => { moveTab(ti, -1); tabPopoverId = null; }} data-action="move-tab-left">{@render icon('arrow-left')}Move left</button>
											<button type="button" disabled={ti === form.tabs.length - 1} onclick={() => { moveTab(ti, 1); tabPopoverId = null; }} data-action="move-tab-right">{@render icon('arrow-right')}Move right</button>
											<hr />
											<button type="button" class="danger" disabled={form.tabs.length <= 1} onclick={() => { deleteTab(ti); tabPopoverId = null; }} data-action="delete-tab">{@render icon('x')}Delete tab</button>
										</div>
									{/if}
								</span>
							{/each}
							<span class="di-tab-add" role="button" tabindex="0" onclick={addTab} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); addTab(); } }} data-action="add-tab">{@render icon('plus', 11)}Add tab</span>
						</div>
						<div class="di-field-list" data-import-form-items>
							{#if activeTab.items.length === 0}
								<div class="di-empty">
									{@render icon('inbox', 24)}
									<div class="di-empty-text">Drop inputs here or click Add on the left</div>
								</div>
							{/if}
							{#each activeTab.items as it, index (it._id)}
								{@render anyItem(it, activeTab.items, index)}
							{/each}
							{@render addMenu(activeTab.items, true)}
						</div>
					</div>
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
				<h3>What shows in history</h3>
				<p class="desc">Pick the form fields whose values are recorded on every generation, and how they read in the history card.</p>

				<div class="hist-designer">
					<div class="hist-left">
						<div class="pane-title">Recorded fields <span class="n">{historyRows.length + 4}</span></div>
						<div class="hist-table" data-history-table>
							<div class="hist-head"><span></span><span></span><span>Field</span><span>Label in history</span><span>Value</span></div>

							<div class="hist-row locked"><span></span><span></span><div class="hist-field"><span class="hist-field-label">Prompt</span></div><input class="hist-label-input" type="text" value="Prompt" disabled /><span class="lock-badge">{@render icon('lock', 10)}always recorded</span></div>
							<div class="hist-row locked"><span></span><span></span><div class="hist-field"><span class="hist-field-label">Negative prompt</span></div><input class="hist-label-input" type="text" value="Negative prompt" disabled /><span class="lock-badge">{@render icon('lock', 10)}always recorded</span></div>
							<div class="hist-row locked"><span></span><span></span><div class="hist-field"><span class="hist-field-label">Seed</span></div><input class="hist-label-input" type="text" value="Seed" disabled /><span class="lock-badge">{@render icon('lock', 10)}always recorded</span></div>
							<div class="hist-row locked"><span></span><span></span><div class="hist-field"><span class="hist-field-label">Quantity</span></div><input class="hist-label-input" type="text" value="Quantity" disabled /><span class="lock-badge">{@render icon('lock', 10)}always recorded</span></div>

							{#each historyRows as row, index (row.field_name)}
								{@const f = fieldForRow(row)}
								<div class="hist-row" class:off={!row.enabled} data-history-row={row.field_name}>
									<div class="hist-reorder">
										<button type="button" class="iconbtn" title="Move up" onclick={() => moveHistoryRow(index, -1)} data-action="move-up">{@render icon('chevron-up', 10)}</button>
										<button type="button" class="iconbtn" title="Move down" onclick={() => moveHistoryRow(index, 1)} data-action="move-down">{@render icon('chevron-down', 10)}</button>
									</div>
									<input type="checkbox" bind:checked={row.enabled} data-action="toggle-emit" />
									<div class="hist-field">
										<span class="hist-field-label">{f?.label || row.field_name}</span>
										<span class="hist-field-name mono">{f?.mappings?.map((m) => m.input_name).join(' · ') || row.field_name}</span>
									</div>
									<input class="hist-label-input" type="text" bind:value={row.label} disabled={!row.enabled} />
									<select class="hist-value-select" value={row.format} onchange={(e) => onFormatChange(row, e.currentTarget.value)} disabled={!row.enabled}>
										{#each FORMAT_OPTIONS as opt}<option value={opt.value}>{opt.label}</option>{/each}
									</select>
									{#if row.enabled && row.format === 'jinja'}
										<div class="hist-jinja-row">
											<span class="lbl">Template</span>
											<input class="hist-jinja-input mono" type="text" bind:value={row.template} />
											<span class="hist-jinja-out">→ <span class="v mono">{f ? formatPreviewValue(f, 'jinja', row.template) : ''}</span></span>
										</div>
									{/if}
								</div>
							{/each}
						</div>
					</div>

					<div class="hist-right">
						<div class="pane-title">History card preview</div>
						<div class="preview-card" data-history-preview>
							<div class="preview-head">
								<div class="t">{@render icon('sliders', 14)}Parameters</div>
								<span class="chip chip-mute">#1</span>
							</div>
							<div class="preview-grid">
								<div class="preview-cell span2"><div class="preview-k">Prompt</div><div class="preview-v wrap">cinematic wide shot of a lighthouse at dusk</div></div>
								<div class="preview-cell span2"><div class="preview-k">Negative prompt</div><div class="preview-v wrap">blurry, low quality</div></div>
								<div class="preview-cell"><div class="preview-k">Seed</div><div class="preview-v tabular">429218</div></div>
								<div class="preview-cell"><div class="preview-k">Quantity</div><div class="preview-v tabular">4</div></div>
								{#each historyRows.filter((r) => r.enabled) as row (row.field_name)}
									{@const f = fieldForRow(row)}
									{#if row.format === 'list' && f && Array.isArray(f.default)}
										<div class="preview-cell span2">
											<div class="preview-k">{row.label}</div>
											<div class="preview-chips">
												{#each f.default as chipItem}<span class="chip chip-info">{typeof chipItem === 'object' ? chipItem?.name || JSON.stringify(chipItem) : chipItem}</span>{/each}
											</div>
										</div>
									{:else}
										<div class="preview-cell"><div class="preview-k">{row.label}</div><div class="preview-v tabular">{f ? formatPreviewValue(f, row.format, row.template) : ''}</div></div>
									{/if}
								{/each}
							</div>
							{#if historyRows.filter((r) => r.enabled).length === 0}
								<div class="preview-empty">
									{@render icon('inbox', 20)}
									<div class="preview-empty-text">Nothing extra will be recorded — prompt, seed and quantity always are.</div>
								</div>
							{/if}
						</div>
					</div>
				</div>
			{:else if step === 4}
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
			{:else if step === 5 && createResult}
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
			{#if step === 5}
				<button type="button" class="btn btn-secondary" onclick={importAnother}>Import another</button>
				<div class="footer-spacer"></div>
				<a class="btn btn-primary" href={`/admin?tab=presets&preset=${createResult?.preset_id ?? ''}`} data-import-open-preset>Open in Presets</a>
			{:else}
				{#if step > 1}
					<button type="button" class="btn btn-secondary" disabled={analyzing || creating} onclick={goBack}>Back</button>
				{/if}
				<div class="footer-spacer"></div>
				{#if step === 1}
					<button type="button" class="btn btn-primary" disabled={analyzing || editLoading || (!(analysis && workflowJson) && !rawText.trim())} onclick={handleSourceContinue} data-import-analyze>
						{analyzing ? 'Analyzing…' : 'Continue'}
					</button>
				{:else if step === 2}
					<button type="button" class="btn btn-primary" disabled={!canContinueForm} onclick={goToHistory} data-import-continue-form>Continue</button>
				{:else if step === 3}
					<button type="button" class="btn btn-primary" onclick={goToRequirementsStep} data-import-continue-history>Continue</button>
				{:else if step === 4}
					<button type="button" class="btn btn-primary" disabled={creating} onclick={runCreate} data-import-create>
						{creating ? (editPresetId ? 'Updating…' : 'Creating…') : editPresetId ? 'Update preset' : 'Continue'}
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
		padding-bottom: 26px;
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
		max-width: 640px;
	}
	.edit-banner {
		padding: 8px 12px;
		margin-bottom: 16px;
		border-radius: 6px;
		background: rgb(var(--signal, 91 157 255) / 0.08);
		border: 1px solid rgb(var(--signal, 91 157 255) / 0.25);
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 12px;
	}
	.edit-banner strong {
		color: rgb(var(--fg, 232 234 237));
		font-weight: 600;
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
		flex-shrink: 0;
	}
	.chip-info {
		background: rgb(var(--info, 91 157 255) / 0.13);
		color: rgb(var(--info, 91 157 255));
	}
	.chip-mute {
		background: rgb(var(--surface-3, 39 42 49));
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.chip-warn {
		background: rgb(var(--warning, 255 197 61) / 0.13);
		color: rgb(var(--warning, 255 197 61));
	}
	.chip-violet {
		background: rgb(var(--violet, 144 133 233) / 0.16);
		color: rgb(var(--violet, 144 133 233));
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

	.iconbtn {
		width: 22px;
		height: 22px;
		border-radius: 4px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		color: rgb(var(--fg-subtle, 122 128 144));
		background: transparent;
		border: 1px solid transparent;
		cursor: pointer;
		flex-shrink: 0;
	}
	.iconbtn:hover,
	.iconbtn.active {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-2, 31 33 38));
	}
	.iconbtn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.name-grid {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 12px;
		margin-top: 16px;
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

	/* ---- Form designer: two panes ---- */
	.designer {
		display: flex;
		gap: 14px;
		align-items: flex-start;
	}
	.di-left {
		width: 40%;
		flex-shrink: 0;
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--canvas, 12 13 15));
		overflow: hidden;
	}
	.di-left-title {
		padding: 10px 12px 8px;
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: rgb(var(--fg-muted, 169 174 184));
	}
	.di-search {
		padding: 0 10px 10px;
	}
	.di-search input {
		width: 100%;
		box-sizing: border-box;
		height: 28px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 12px;
		padding: 0 9px;
	}
	.di-group-h {
		padding: 7px 12px;
		background: rgb(var(--surface-1, 22 24 28));
		border-top: 1px solid rgb(var(--line, 36 38 44));
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
		font-size: 11.5px;
		font-weight: 600;
		color: rgb(var(--fg, 232 234 237));
	}
	.di-group-h .cls {
		font-size: 10px;
		font-weight: 400;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin-left: 4px;
	}
	.di-row {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 7px 12px;
	}
	.di-row + .di-row {
		border-top: 1px solid rgb(var(--line, 36 38 44) / 0.5);
	}
	.di-row-name {
		font-size: 11px;
		color: rgb(var(--fg, 232 234 237));
		flex-shrink: 0;
		width: 76px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.di-row-value {
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.di-row.mapped .di-row-name,
	.di-row.mapped .di-row-value {
		color: rgb(var(--fg-disabled, 92 98 112));
	}
	.di-row-trail {
		flex-shrink: 0;
		width: 22px;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.di-add-arrow {
		color: rgb(var(--signal, 91 157 255));
	}
	.lock-badge {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-size: 9px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-disabled, 92 98 112));
		flex-shrink: 0;
		white-space: nowrap;
	}

	.di-right {
		flex: 1;
		min-width: 0;
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--surface-1, 22 24 28));
		overflow: hidden;
		display: flex;
		flex-direction: column;
	}
	.di-tabs {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 8px 10px 0;
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
		flex-wrap: wrap;
	}
	.di-tab {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 7px 10px;
		border-radius: 4px 4px 0 0;
		font-size: 12px;
		font-weight: 500;
		color: rgb(var(--fg-muted, 169 174 184));
		cursor: pointer;
		position: relative;
	}
	.di-tab.active {
		background: rgb(var(--signal, 91 157 255) / 0.1);
		color: rgb(var(--signal, 91 157 255));
	}
	.di-tab .kb {
		width: 16px;
		height: 16px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		color: rgb(var(--fg-subtle, 122 128 144));
		border-radius: 3px;
	}
	.di-tab .kb:hover {
		background: rgb(var(--surface-3, 39 42 49));
		color: rgb(var(--fg, 232 234 237));
	}
	.di-tab-rename {
		height: 20px;
		width: 90px;
		border-radius: 3px;
		border: 1px solid rgb(var(--signal, 91 157 255) / 0.4);
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 12px;
		padding: 0 6px;
	}
	.di-tab-add {
		padding: 7px 10px;
		color: rgb(var(--fg-subtle, 122 128 144));
		font-size: 12px;
		display: flex;
		align-items: center;
		gap: 4px;
		cursor: pointer;
	}
	.di-tab-add:hover {
		color: rgb(var(--fg, 232 234 237));
	}

	.di-field-list {
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		flex: 1;
	}
	.di-field-card {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--canvas, 12 13 15));
		padding: 9px 10px;
	}
	.di-field-top {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		row-gap: 6px;
	}
	.di-field-label {
		width: 110px;
		flex-shrink: 0;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 12px;
		font-weight: 500;
		padding: 0 8px;
	}
	.di-field-type {
		width: 100px;
		flex-shrink: 0;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 11px;
		padding: 0 6px;
	}
	.di-field-default {
		flex: 1 1 90px;
		min-width: 90px;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11.5px;
		padding: 0 8px;
	}
	.di-field-actions {
		display: flex;
		align-items: center;
		gap: 1px;
		flex-shrink: 0;
	}
	.di-mapping {
		margin-top: 6px;
		padding-left: 24px;
	}
	.di-mapping .line {
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.di-mapping .arrow {
		color: rgb(var(--fg-disabled, 92 98 112));
		margin-right: 3px;
	}

	.di-add-wrap {
		position: relative;
	}
	.di-add-field {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		height: 34px;
		width: 100%;
		border: 1px dashed rgb(var(--line-strong, 43 46 53));
		border-radius: 6px;
		font-size: 11.5px;
		color: rgb(var(--fg-muted, 169 174 184));
		background: none;
		cursor: pointer;
	}
	.di-add-field:hover {
		color: rgb(var(--fg, 232 234 237));
	}
	.di-add-menu {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		width: 168px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		border-radius: 8px;
		padding: 4px;
		background: rgb(var(--surface-1, 22 24 28));
		box-shadow: var(--shadow-floating, 0 4px 16px rgb(0 0 0 / 0.5));
		z-index: 20;
	}
	.di-add-menu button {
		display: flex;
		width: 100%;
		align-items: center;
		gap: 8px;
		border-radius: 5px;
		padding: 6px 8px;
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
		background: none;
		border: none;
		text-align: left;
		cursor: pointer;
	}
	.di-add-menu button:hover {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-3, 39 42 49));
	}
	.di-add-menu .type-tag {
		margin-left: auto;
		font-size: 8.5px;
		color: rgb(var(--fg-disabled, 92 98 112));
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}

	.di-container {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--surface-2, 31 33 38) / 0.4);
	}
	.di-container-head {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px 10px;
		flex-wrap: wrap;
	}
	.di-container-title {
		flex-shrink: 0;
		width: 130px;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 12px;
		font-weight: 600;
		padding: 0 8px;
	}
	.di-container-spacer {
		flex: 1;
	}
	.di-container-actions {
		display: flex;
		align-items: center;
		gap: 1px;
		flex-shrink: 0;
	}
	.di-container-body {
		padding: 0 10px 10px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.di-container.collapsed .di-container-body {
		display: none;
	}
	.di-chevron {
		display: inline-flex;
		transition: transform 0.12s;
	}
	.di-chevron.collapsed {
		transform: rotate(-90deg);
	}
	.di-row-cols {
		display: grid;
		gap: 8px;
		min-width: 0;
	}
	.di-row-cols > :global(*) {
		min-width: 0;
	}
	.di-row-cols :global(.di-field-card) {
		overflow: hidden;
	}
	.di-row-cols :global(.di-field-top) {
		flex-wrap: wrap;
	}
	.di-col-control {
		display: flex;
		align-items: center;
		gap: 5px;
	}
	.di-col-control .lbl {
		font-size: 10px;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin-right: 2px;
	}
	.di-col-btn {
		width: 22px;
		height: 22px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 11px;
		font-weight: 600;
		cursor: pointer;
	}
	.di-col-btn.active {
		background: rgb(var(--signal, 91 157 255) / 0.15);
		color: rgb(var(--signal, 91 157 255));
		border-color: rgb(var(--signal, 91 157 255) / 0.4);
	}
	.di-header-card {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px 10px;
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--surface-2, 31 33 38) / 0.4);
	}
	.di-header-input {
		flex: 1;
		min-width: 0;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 12.5px;
		font-weight: 600;
		padding: 0 8px;
	}

	.di-empty {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 8px;
		padding: 40px 20px;
		text-align: center;
	}
	.di-empty :global(.icon) {
		color: rgb(var(--fg-disabled, 92 98 112));
	}
	.di-empty-text {
		font-size: 12px;
		color: rgb(var(--fg-subtle, 122 128 144));
	}

	.di-mapedit {
		margin-top: 8px;
		padding-top: 10px;
		border-top: 1px dashed rgb(var(--line, 36 38 44));
	}
	.di-mapedit-label {
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		margin-bottom: 6px;
	}
	.di-mapedit-list {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 4px;
		overflow: hidden;
	}
	.di-mapedit-row {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 6px 9px;
		font-size: 11px;
		flex-wrap: wrap;
	}
	.di-mapedit-node,
	.di-mapedit-input {
		white-space: nowrap;
	}
	.di-mapedit-row + .di-mapedit-row {
		border-top: 1px solid rgb(var(--line, 36 38 44));
	}
	.di-mapedit-row.selected {
		background: rgb(var(--signal, 91 157 255) / 0.08);
	}
	.di-mapedit-row input[type='checkbox'] {
		accent-color: rgb(var(--signal, 91 157 255));
	}
	.di-mapedit-node {
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.di-mapedit-input {
		color: rgb(var(--fg, 232 234 237));
	}
	.di-mapedit-taken {
		margin-left: auto;
		font-size: 10px;
		color: rgb(var(--fg-disabled, 92 98 112));
	}
	.di-mapedit-transform {
		margin-left: auto;
		flex: 1 1 100%;
	}
	.di-mapedit-transform select {
		box-sizing: border-box;
		width: 100%;
		height: 24px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11px;
		padding: 0 6px;
	}

	.tab-popover {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		width: 150px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		border-radius: 8px;
		padding: 4px;
		background: rgb(var(--surface-1, 22 24 28));
		box-shadow: var(--shadow-floating, 0 4px 16px rgb(0 0 0 / 0.5));
		z-index: 20;
	}
	.tab-popover button {
		display: flex;
		width: 100%;
		align-items: center;
		gap: 8px;
		border-radius: 5px;
		padding: 6px 8px;
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
		background: none;
		border: none;
		text-align: left;
		cursor: pointer;
	}
	.tab-popover button:hover:not(:disabled) {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-3, 39 42 49));
	}
	.tab-popover button:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.tab-popover hr {
		border: none;
		border-top: 1px solid rgb(var(--line, 36 38 44));
		margin: 4px 2px;
	}
	.tab-popover button.danger {
		color: rgb(var(--danger, 255 138 138));
	}

	/* ---- History step ---- */
	.hist-designer {
		display: flex;
		gap: 14px;
		align-items: flex-start;
	}
	.hist-left {
		width: 58%;
		flex-shrink: 0;
	}
	.hist-right {
		flex: 1;
		min-width: 0;
	}
	.pane-title {
		padding: 0 0 8px;
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: rgb(var(--fg-muted, 169 174 184));
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.pane-title .n {
		font-weight: 400;
		color: rgb(var(--fg-disabled, 92 98 112));
		text-transform: none;
		letter-spacing: 0;
	}
	.hist-table {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--canvas, 12 13 15));
		overflow: hidden;
	}
	.hist-head {
		display: grid;
		grid-template-columns: 40px 18px 1.3fr 1.05fr 1.05fr;
		align-items: center;
		gap: 10px;
		padding: 7px 12px;
		background: rgb(var(--surface-1, 22 24 28));
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.hist-row {
		display: grid;
		grid-template-columns: 40px 18px 1.3fr 1.05fr 1.05fr;
		align-items: center;
		gap: 10px;
		padding: 8px 12px;
	}
	.hist-row + .hist-row {
		border-top: 1px solid rgb(var(--line, 36 38 44) / 0.6);
	}
	.hist-row.locked {
		background: rgb(var(--surface-1, 22 24 28) / 0.5);
	}
	.hist-reorder {
		display: flex;
		gap: 2px;
	}
	.hist-row input[type='checkbox'] {
		accent-color: rgb(var(--signal, 91 157 255));
		width: 14px;
		height: 14px;
	}
	.hist-field {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 1px;
	}
	.hist-field-label {
		font-size: 12px;
		font-weight: 500;
		color: rgb(var(--fg, 232 234 237));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.hist-row.off .hist-field-label {
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.hist-field-name {
		font-size: 9.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.hist-label-input {
		width: 100%;
		box-sizing: border-box;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11.5px;
		padding: 0 8px;
	}
	.hist-label-input:disabled {
		background: transparent;
		border-color: transparent;
		color: rgb(var(--fg-disabled, 92 98 112));
		padding-left: 0;
	}
	.hist-value-select {
		width: 100%;
		box-sizing: border-box;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg-muted, 169 174 184));
		font-size: 11px;
		padding: 0 6px;
	}
	.hist-value-select:disabled {
		opacity: 0.5;
	}
	.hist-jinja-row {
		grid-column: 1 / -1;
		margin: 2px 0 0 58px;
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.hist-jinja-row .lbl {
		font-size: 10px;
		color: rgb(var(--fg-subtle, 122 128 144));
		flex-shrink: 0;
	}
	.hist-jinja-input {
		flex: 1;
		box-sizing: border-box;
		height: 26px;
		border-radius: 4px;
		border: 1px solid rgb(var(--signal, 91 157 255) / 0.4);
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11px;
		padding: 0 8px;
	}
	.hist-jinja-out {
		font-size: 10px;
		color: rgb(var(--fg-subtle, 122 128 144));
		flex-shrink: 0;
	}
	.hist-jinja-out .v {
		color: rgb(var(--success, 61 214 140));
	}

	.preview-card {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 6px;
		background: rgb(var(--surface-2, 31 33 38));
		overflow: hidden;
		box-shadow: var(--shadow-raised, inset 0 1px 0 rgb(255 255 255 / 0.04));
	}
	.preview-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 10px 12px;
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
	}
	.preview-head .t {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		font-weight: 600;
		color: rgb(var(--fg, 232 234 237));
	}
	.preview-head .t :global(.icon) {
		color: rgb(var(--success, 61 214 140));
	}
	.preview-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1px;
		background: rgb(var(--line, 36 38 44));
	}
	.preview-cell {
		background: rgb(var(--surface-2, 31 33 38));
		padding: 10px;
		min-width: 0;
	}
	.preview-cell.span2 {
		grid-column: 1 / -1;
	}
	.preview-k {
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-disabled, 92 98 112));
		margin-bottom: 4px;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}
	.preview-v {
		font-size: 11.5px;
		font-weight: 600;
		color: rgb(var(--fg, 232 234 237));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}
	.preview-v.wrap {
		white-space: normal;
		overflow: visible;
		text-overflow: clip;
	}
	.preview-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 2px;
	}
	.preview-empty {
		padding: 20px 16px;
		text-align: center;
	}
	.preview-empty :global(.icon) {
		color: rgb(var(--fg-disabled, 92 98 112));
		margin: 0 auto 8px;
	}
	.preview-empty-text {
		font-size: 11.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		max-width: 260px;
		margin: 0 auto;
	}

	:global(.icon) {
		stroke: currentColor;
		fill: none;
		flex: none;
		display: block;
	}
</style>
