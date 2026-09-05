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
	import { onMount, onDestroy } from 'svelte';
	import { floating } from './floating.js';

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

	// Curated for the tabs shipped presets actually use (see defaults.py's
	// build_default_form) - a symbol exists in this file's sprite for each,
	// under its own `ti-` namespace so a name here never collides with the
	// generic `icon()` snippet's UI-chrome sprite (e.g. `sliders` already
	// names an unrelated glyph there).
	const TAB_ICON_OPTIONS = [
		{ value: '', label: 'None' },
		{ value: 'generation', label: 'Generation' },
		{ value: 'settings', label: 'Settings' },
		{ value: 'lora', label: 'LoRA' },
		{ value: 'model', label: 'Model' },
		{ value: 'image', label: 'Image' },
		{ value: 'video', label: 'Video' },
		{ value: 'film', label: 'Film' },
		{ value: 'sparkles', label: 'Sparkles' },
		{ value: 'embedding', label: 'Embedding' },
		{ value: 'sliders', label: 'Sliders' },
		{ value: 'face', label: 'Face' },
		{ value: 'warning', label: 'Warning' },
		{ value: 'information-circle', label: 'Info' }
	];

	const TAB_DISPLAY_OPTIONS = [
		{ value: 'icon_only', label: 'Icon only' },
		{ value: 'icon_label', label: 'Icon + label' },
		{ value: 'label', label: 'Label only' }
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

	// Bumped whenever the current source is retired (a new file picked, an
	// edit load started, "Change workflow", "Import another", or teardown).
	// Every async response boundary below (file read, edit-source load,
	// analyze, requirements preview, create) captures this value before
	// awaiting and checks it before writing - a response for a source no
	// longer current is dropped instead of clobbering whatever replaced it.
	// Local-only (never leaves this component) and NOT a draft identity - it
	// restarts at 0 on every mount, so two different mounts can land on the
	// same value (see `draftId` below for the identity that leaves the
	// component).
	let sourceToken = 0;
	// sourceToken value the in-flight/last requirements request belongs to,
	// so goToRequirementsStep's "already have it" guard isn't fooled by a
	// retired source's requirementsLoading/requirementsResults.
	let requirementsToken = null;

	// Globally-unique identity for the draft currently loaded (sent to the
	// chat assistant as `draft_id` - see buildImportChatContext). Unlike
	// sourceToken this must never collide across mounts/instances, since a
	// stale proposal's draft_id is trusted as proof of which draft it was
	// built for; two different imports racing to stamp the same small
	// integer would let one apply onto the other silently.
	function mintDraftId() {
		if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
		return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
	}
	let draftId = $state(mintDraftId());

	// Every user action that changes or discards the current source - typing
	// or pasting into the source textarea, picking/dropping a new file,
	// starting an edit-source load, "Change workflow", "Import another", and
	// component teardown - routes through here. It bumps sourceToken (so any
	// response already in flight for the retired source is ignored on
	// arrival by that request's own token check) AND immediately releases
	// the loading flags the retired request owns, instead of leaving them
	// stuck until - or unless - that request's `finally` happens to run: a
	// pending analyze/edit-load/requirements-preview/create for a source the
	// user has already moved on from must not wedge the UI (analyzing stuck
	// true blocks every later "Continue", etc). It also retires the current
	// draft identity - any propose_form_changes result stamped with the
	// draft_id this replaces is reported stale, never applied.
	//
	// It also invalidates a COMPLETED analysis: `analysis`/`workflowJson`
	// are nulled unconditionally, so handleSourceContinue's "already
	// analyzed, just advance" shortcut can only fire when the source truly
	// hasn't changed since that analysis - i.e. plain Back navigation, which
	// never calls this. A source change after a completed analyze (edit the
	// textarea again, pick another file) must re-analyze, not reuse the old
	// result under the new source's identity.
	function retireSource() {
		sourceToken += 1;
		draftId = mintDraftId();
		analyzing = false;
		editLoading = false;
		requirementsToken = null;
		requirementsLoading = false;
		creating = false;
		analysis = null;
		workflowJson = null;
		pendingFileRead = false;
	}

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
	// True from the moment a file is picked/dropped until its FileReader
	// settles (or the source is retired again) - the selected file, not
	// whatever the textarea still shows, owns the source while this is true,
	// so Continue must not analyze the stale textarea value out from under
	// a read that hasn't landed yet.
	let pendingFileRead = $state(false);

	// ---- Step 2: Form ----
	let _uidCounter = 0;
	function uid(prefix) {
		_uidCounter += 1;
		return `${prefix}_${_uidCounter}`;
	}

	function emptyForm() {
		return { tabs: [{ id: 'generation', label: 'Generation', icon: null, icon_display: 'label', items: [] }], lora_chain: null };
	}

	let form = $state(emptyForm());
	// The LoRA chain field this session's "Convert to LoRA picker" created
	// (or hydrated from an existing preset's `form.lora_chain`) - tracked by
	// name, not object reference, so it survives hydrate/dehydrate round
	// trips. Cleared (and `form.lora_chain` with it) the moment that field is
	// removed from the form - see the $effect below.
	let loraPickerFieldName = $state(null);
	// Pre-conversion only: which chain node ids the "Keep fixed" toggle has
	// marked, read by `convertLoraChainToPicker` at conversion time. Once
	// converted, kept/replaced live on `form.lora_chain` instead.
	let loraKeepFixed = $state(new Set());
	let activeTabId = $state('generation');
	let fieldTypeOptions = $state([]);
	// type -> the full `/api/fields/types` manifest entry (configuration_schema
	// included) - fieldTypeOptions above stays a plain name list for the type
	// <select>, this is what the config editor below reads.
	let fieldTypeManifest = $state({});
	// Free-typed drafts for a config row's list/JSON textarea, keyed by
	// `${item._id}::${spec.name}` - kept apart from `item.config` so an
	// in-progress line or an unparsable JSON edit isn't clobbered by the
	// canonical value on every keystroke.
	let configListDrafts = $state({});
	let configJsonDrafts = $state({});
	let configJsonErrors = $state({});
	let families = $state([]);
	let modelFamily = $state('');
	let variant = $state('imported');
	let displayName = $state('');
	let leftSearch = $state('');
	let renamingTabId = $state(null);
	let tabPopoverId = $state(null);
	let tabPopoverAnchorEl = $state(null);
	let addMenuOpenFor = $state(null);
	let addMenuAnchorEl = $state(null);
	let expandedFieldId = $state(null);
	let dismissedSuggestionIds = $state({});

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
	let loraChainNodes = $derived(analysis?.lora_chain?.nodes || []);
	let loraReplacedIds = $derived(new Set(form.lora_chain?.replaced_node_ids || []));
	let loraKeptIds = $derived(new Set(form.lora_chain?.kept_node_ids || []));
	let loraConverted = $derived(
		!!form.lora_chain && !!loraPickerFieldName && allFields.some((f) => f.field_name === loraPickerFieldName && f.field_type === 'lora_picker')
	);
	// A kept node with a to-be-replaced node both before and after it (in
	// chain order) is a shape the picker's flat loop can't represent - see
	// backend.preset_import.schema._find_sandwiched_kept_nodes's docstring.
	// Only meaningful pre-conversion, while `loraKeepFixed` is still being
	// arranged; once converted the split is final and validated server-side.
	let loraSandwichError = $derived(
		loraConverted ? null : findSandwichedLoraNode(loraChainNodes, loraKeepFixed)
	);
	// True once every detected chain node is marked kept (either mid-toggle
	// or, post-conversion, because the replaced set ended up empty) - the
	// splice point moves from "replace in place" to "after the last kept
	// node", which is worth calling out next to the Convert button.
	let loraAllKept = $derived(
		loraChainNodes.length > 0 &&
			(loraConverted ? loraReplacedIds.size === 0 : loraChainNodes.every((n) => loraKeepFixed.has(n.node_id)))
	);
	// The model-chain edge the emitter splices a picker's `@loop` into when
	// there is nothing to replace (no chain at all, or every node kept) -
	// see backend's `analysis.model_chain`. `null` when the workflow has no
	// sampler cluster to splice before.
	let modelChainInfo = $derived(analysis?.model_chain || null);
	let leftGroups = $derived(
		buildLeftGroups(
			(analysis?.candidates || []).filter((c) => !loraReplacedIds.has(c.node_id)),
			leftSearch
		)
	);
	let mappableCandidates = $derived((analysis?.candidates || []).filter((c) => !isLockedCandidate(c) && !loraReplacedIds.has(c.node_id)));
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

	// Name-based mapping suggestions: "seed" (a workflow input) suggests
	// itself for a field also named "seed" - a nudge, not an auto-apply.
	function normalizeMatchName(s) {
		return (s || '').toString().trim().toLowerCase().replace(/[-\s]+/g, '_');
	}

	function candidateNameMatchesField(c, field) {
		if (mappedKeySet.has(candidateKey(c))) return false;
		const normInput = normalizeMatchName(c.input_name);
		return normInput === normalizeMatchName(field.field_name) || normInput === normalizeMatchName(field.label);
	}

	function nameMatchSuggestionsFor(field) {
		if (field.mappings.length > 0) return [];
		return mappableCandidates.filter((c) => candidateNameMatchesField(c, field)).slice(0, 3);
	}

	function dismissSuggestion(fieldId) {
		dismissedSuggestionIds = { ...dismissedSuggestionIds, [fieldId]: true };
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
		const icon = t.icon ?? null;
		// A draft saved before icon_display existed carries no such key -
		// fall back to the same icon-implies-icon_only rule the schema's
		// FormTab validator applies server-side, so an old sidecar still
		// loads with a sane display mode.
		const icon_display = t.icon_display ?? (icon ? 'icon_only' : 'label');
		return { id: t.id || uid('tab'), label: t.label || 'Tab', icon, icon_display, items: (t.items || []).map(hydrateItem) };
	}

	function hydrateForm(raw) {
		const tabs = raw?.tabs?.length ? raw.tabs.map(hydrateTab) : emptyForm().tabs;
		const lora_chain = raw?.lora_chain
			? {
					replaced_node_ids: [...(raw.lora_chain.replaced_node_ids || [])],
					kept_node_ids: [...(raw.lora_chain.kept_node_ids || [])]
				}
			: null;
		return { tabs, lora_chain };
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
		return {
			tabs: form.tabs.map((t) => ({
				id: t.id,
				label: t.label,
				icon: t.icon ?? null,
				icon_display: t.icon_display || (t.icon ? 'icon_only' : 'label'),
				items: t.items.map(dehydrateItem)
			})),
			lora_chain: form.lora_chain
				? { replaced_node_ids: [...form.lora_chain.replaced_node_ids], kept_node_ids: [...form.lora_chain.kept_node_ids] }
				: null
		};
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

		if (form.lora_chain) {
			const pickerField = collectFields(form).find((f) => f.field_type === 'lora_picker');
			loraPickerFieldName = pickerField ? pickerField.field_name : null;
			loraKeepFixed = new Set(form.lora_chain.kept_node_ids || []);
			if (!loraPickerFieldName) form.lora_chain = null; // stale/incomplete sidecar - start clean
		} else {
			loraPickerFieldName = null;
			loraKeepFixed = new Set();
		}
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
		return c.suggested_transform || 'none';
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

	// A node's class type via whatever the wizard already has in hand
	// (a candidate row, then the raw API-format workflow), falling back to
	// its bare id - used for the "no LoRA nodes detected" splice hint.
	function nodeClassLabel(nodeId) {
		if (!nodeId) return null;
		const candidate = (analysis?.candidates || []).find((c) => c.node_id === nodeId);
		if (candidate?.class_type) return candidate.class_type;
		const raw = workflowJson && typeof workflowJson === 'object' ? workflowJson[nodeId] : null;
		if (raw && typeof raw === 'object' && raw.class_type) return raw.class_type;
		return nodeId;
	}

	let modelChainInsertionLabel = $derived(
		modelChainInfo ? `${nodeClassLabel(modelChainInfo.target_node_id)}.${modelChainInfo.target_input}` : ''
	);

	// ---- LoRA chain -> lora_picker conversion ----
	function toggleLoraKeepFixed(nodeId) {
		if (loraConverted) return;
		const next = new Set(loraKeepFixed);
		if (next.has(nodeId)) next.delete(nodeId);
		else next.add(nodeId);
		loraKeepFixed = next;
	}

	// `(kept_node_id, nearest_replaced_before, nearest_replaced_after)` for
	// the first kept chain node with a to-be-replaced node both before AND
	// after it in `nodes`' source -> target order, or `null` - mirrors
	// backend.preset_import.schema._find_sandwiched_kept_nodes exactly (see
	// its docstring for why this shape is rejected rather than silently
	// dropping the kept node from the live compute path).
	function findSandwichedLoraNode(nodes, keepFixedSet) {
		const order = nodes.map((n) => n.node_id);
		for (let i = 0; i < order.length; i++) {
			const nodeId = order[i];
			if (!keepFixedSet.has(nodeId)) continue;
			let beforeId = null;
			for (let j = i - 1; j >= 0; j--) {
				if (!keepFixedSet.has(order[j])) { beforeId = order[j]; break; }
			}
			let afterId = null;
			for (let j = i + 1; j < order.length; j++) {
				if (!keepFixedSet.has(order[j])) { afterId = order[j]; break; }
			}
			if (beforeId !== null && afterId !== null) return { nodeId, beforeId, afterId };
		}
		return null;
	}

	// Shared by the chain-conversion flow below and a field card's own
	// type-change reset (configForTypeChange) - both need the exact same
	// picker config shape.
	function loraPickerConfig() {
		return {
			model_type: 'lora',
			placeholder: 'Select a LoRA...',
			allow_info_modal: true,
			strength_min: -2.0,
			strength_max: 2.0,
			strength_step: 0.1,
			strength_default: 1.0,
			max_items: 6
		};
	}

	// Shared by the wizard's own "Convert to LoRA picker" button and the
	// assistant's `lora_picker` tool op (see applyImportFormChanges) - the
	// only difference between the two call sites is where `keepFixedIds`
	// comes from.
	function applyLoraPickerConversion(tab, keepFixedIds, fieldNameHint) {
		const nodes = loraChainNodes;
		if (!tab || loraConverted) return false;
		// No detected chain at all is only fine when there's somewhere to
		// splice the picker's own `@loop` - i.e. a model_chain edge into a
		// sampler cluster. Otherwise there's nothing to wire it into.
		if (!nodes.length && !modelChainInfo) return false;
		if (findSandwichedLoraNode(nodes, keepFixedIds)) return false;
		const replaced = nodes.filter((n) => !keepFixedIds.has(n.node_id));
		const kept = nodes.filter((n) => keepFixedIds.has(n.node_id));
		const fieldName = uniqueFieldName(fieldNameHint || 'loras');
		const field = {
			_id: uid('item'),
			kind: 'field',
			field_name: fieldName,
			field_type: 'lora_picker',
			label: 'LoRAs',
			default: replaced.map((n) => ({
				model: `models/loras/${n.lora_name || ''}`,
				strength: typeof n.strength_model === 'number' ? n.strength_model : 1
			})),
			config: loraPickerConfig(),
			mappings: []
		};
		tab.items.push(field);
		form.lora_chain = {
			replaced_node_ids: replaced.map((n) => n.node_id),
			kept_node_ids: kept.map((n) => n.node_id)
		};
		loraPickerFieldName = fieldName;
		return true;
	}

	function convertLoraChainToPicker() {
		applyLoraPickerConversion(activeTab || form.tabs[0], loraKeepFixed, 'loras');
	}

	function addLoraPickerNoChain() {
		applyLoraPickerConversion(activeTab || form.tabs[0], new Set(), 'loras');
	}

	// A field removed by any path (the field card's Remove button, deleting
	// its tab, ...) that happened to be the chain's picker un-marks every
	// row - the chain goes back to "not yet converted", not a dangling
	// selection pointing at a field that no longer exists.
	$effect(() => {
		if (loraPickerFieldName && !allFields.some((f) => f.field_name === loraPickerFieldName)) {
			form.lora_chain = null;
			loraPickerFieldName = null;
			loraKeepFixed = new Set();
		}
	});

	function addTab() {
		let n = form.tabs.length + 1;
		let id = `tab_${n}`;
		while (form.tabs.some((t) => t.id === id)) {
			n += 1;
			id = `tab_${n}`;
		}
		form.tabs.push({ id, label: `Tab ${form.tabs.length + 1}`, icon: null, icon_display: 'label', items: [] });
		activeTabId = id;
	}

	// Picking an icon on a label-only tab switches it to icon_only (there's
	// now something for icon_only to show); clearing the icon always drops
	// back to label - mirrors FormTab._icon_display_needs_an_icon server-side.
	function setTabIcon(tab, value) {
		const icon = value || null;
		if (!icon) tab.icon_display = 'label';
		else if (tab.icon_display === 'label') tab.icon_display = 'icon_only';
		tab.icon = icon;
	}

	function setTabDisplay(tab, value) {
		tab.icon_display = value;
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

	function toggleTabPopover(tabId, anchorEl) {
		if (tabPopoverId === tabId) {
			tabPopoverId = null;
		} else {
			tabPopoverId = tabId;
			tabPopoverAnchorEl = anchorEl;
		}
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

	// Finds `{ items, index }` for the item with this `_id` anywhere in the
	// form (any tab, any nesting depth) - shared by the "Move to" popover and
	// the tab-header drop target so both resolve a target item the same way.
	function locateItemInForm(id) {
		function search(items) {
			for (let i = 0; i < items.length; i++) {
				if (items[i]._id === id) return { items, index: i };
				if (items[i].items) {
					const found = search(items[i].items);
					if (found) return found;
				}
			}
			return null;
		}
		for (const t of form.tabs) {
			const found = search(t.items);
			if (found) return found;
		}
		return null;
	}

	// Splices the item out of its current container and appends it to the
	// target tab's root `items`, keeping the same object so `_id` and
	// mappings survive. Shared by the per-item "Move to" popover and the
	// tab-header drop target.
	function moveItemToTab(parentItems, index, targetTabId) {
		const targetTab = form.tabs.find((t) => t.id === targetTabId);
		if (!targetTab || parentItems === targetTab.items) return;
		const [it] = parentItems.splice(index, 1);
		targetTab.items.push(it);
		activeTabId = targetTabId;
	}

	let moveToPopoverId = $state(null);
	let moveToPopoverAnchorEl = $state(null);

	function toggleMoveToPopover(itemId, anchorEl) {
		if (moveToPopoverId === itemId) {
			moveToPopoverId = null;
		} else {
			moveToPopoverId = itemId;
			moveToPopoverAnchorEl = anchorEl;
		}
	}

	// ---- Cross-tab drag and drop (any item kind, any depth) - the grip is
	// the drag handle, tab headers are the drop targets. In-tab reordering
	// stays Move up/down; this is cross-tab only.
	let draggedItemId = $state(null);
	let dragOverTabId = $state(null);

	function handleItemDragStart(e, itemId) {
		draggedItemId = itemId;
		e.dataTransfer.effectAllowed = 'move';
		e.dataTransfer.setData('text/plain', itemId);
	}

	function handleItemDragEnd() {
		draggedItemId = null;
	}

	function handleTabDragOver(e, tabId) {
		if (!draggedItemId) return;
		e.preventDefault();
		dragOverTabId = tabId;
	}

	function handleTabDragLeave(tabId) {
		if (dragOverTabId === tabId) dragOverTabId = null;
	}

	function handleTabDrop(e, tabId) {
		e.preventDefault();
		const id = draggedItemId || e.dataTransfer.getData('text/plain');
		dragOverTabId = null;
		draggedItemId = null;
		if (!id) return;
		const loc = locateItemInForm(id);
		if (loc) moveItemToTab(loc.items, loc.index, tabId);
	}

	function addItemToContainer(items, kind) {
		if (kind === 'field') items.push({ _id: uid('item'), kind: 'field', field_name: uniqueFieldName('field'), field_type: 'text', label: 'New field', default: null, config: null, mappings: [] });
		else if (kind === 'row') items.push({ _id: uid('item'), kind: 'row', columns: 2, items: [] });
		else if (kind === 'group') items.push({ _id: uid('item'), kind: 'group', title: 'Group', items: [] });
		else if (kind === 'section') items.push({ _id: uid('item'), kind: 'section', title: 'Section', collapsed: false, items: [] });
		else if (kind === 'header') items.push({ _id: uid('item'), kind: 'header', text: 'Header' });
		addMenuOpenFor = null;
	}

	function toggleAddMenu(items, anchorEl) {
		if (addMenuOpenFor === items) {
			addMenuOpenFor = null;
		} else {
			addMenuOpenFor = items;
			addMenuAnchorEl = anchorEl;
		}
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

	// ---- Chat assistant bridge: "comfyui-import" mode ----
	// `window.__potionui.chat` is absent on an older host build - every entry
	// point here no-ops rather than throwing when it's missing. Wire contract
	// (context payload shape, op shapes, draft_id/form_revision) is documented
	// on the plugin's `backend.chat.tools` module - keep this in sync with it.
	// `draft_id` is a dedicated globally-unique id (`mintDraftId()`/`draftId`
	// above) minted at component init and on every `retireSource()` - NOT
	// `sourceToken`, which restarts at 0 per mount and would let two
	// different mounts' drafts collide on the same stamped value.
	function serializeImportItem(it) {
		if (it.kind === 'field') {
			return {
				kind: 'field',
				field_name: it.field_name,
				label: it.label,
				field_type: it.field_type,
				mappings: (it.mappings || []).map((m) => ({ node_id: m.node_id, input_name: m.input_name, transform: m.transform || 'none' }))
			};
		}
		if (it.kind === 'header') return { kind: 'header', text: it.text };
		return { kind: it.kind, label: it.title ?? null, items: (it.items || []).map(serializeImportItem) };
	}

	// A fuller fingerprint than `serializeImportItem` (which is the wire
	// shape sent as `form` below, and stays exactly that) - covers every
	// property a manual edit or an assistant proposal can change: tab id and
	// label, field name/label/type/default/config, mappings and their
	// transforms. Only used locally to compute `formRevision`, never sent.
	function revisionSignatureForItem(it) {
		if (it.kind === 'field') {
			return {
				kind: 'field',
				field_name: it.field_name,
				label: it.label,
				field_type: it.field_type,
				default: it.default ?? null,
				config: it.config ?? null,
				mappings: (it.mappings || []).map((m) => ({ node_id: m.node_id, input_name: m.input_name, transform: m.transform || 'none' }))
			};
		}
		if (it.kind === 'header') return { kind: 'header', text: it.text };
		return { kind: it.kind, label: it.title ?? null, items: (it.items || []).map(revisionSignatureForItem) };
	}

	// Bumped whenever the form's revision signature changes (tab id/label
	// add-remove-rename, field name/label/type/default/config edits, mapping
	// toggles, transforms, lora conversion), whether from a manual edit or an
	// approved assistant proposal. Sent alongside `draft_id` so a
	// `propose_form_changes` result approved after the form moved on can be
	// told apart from one approved with nothing having changed since -
	// consulted (not just carried) in applyImportFormChanges, which reports
	// a skipped op as "the form changed" only when this actually drifted.
	let formRevision = $state(0);
	let _formRevisionSignature = null;
	$effect(() => {
		const signature = JSON.stringify(form.tabs.map((t) => ({ id: t.id, label: t.label, items: t.items.map(revisionSignatureForItem) })));
		if (_formRevisionSignature !== null && signature !== _formRevisionSignature) formRevision += 1;
		_formRevisionSignature = signature;
	});

	function buildImportChatContext() {
		if (!analysis) return null;
		return {
			draft_id: draftId,
			form_revision: formRevision,
			workflow_name: displayName || '',
			format: analysis.format,
			node_count: analysis.node_count,
			candidates: (analysis.candidates || []).map((c) => ({
				node_id: c.node_id,
				class_type: c.class_type,
				node_title: c.node_title,
				input_name: c.input_name,
				current_value: c.current_value,
				value_type: c.value_type,
				suggested_field_type: c.suggested_field_type,
				role: c.role,
				locked: isLockedCandidate(c)
			})),
			form: { tabs: form.tabs.map((tab) => ({ id: tab.id, label: tab.label, items: tab.items.map(serializeImportItem) })) },
			mapped: allFields.flatMap((f) =>
				f.mappings.map((m) => ({ field_name: f.field_name, node_id: m.node_id, input_name: m.input_name, transform: m.transform || 'none' }))
			),
			lora_chain: loraChainNodes.length
				? {
						nodes: loraChainNodes.map((n) => ({
							node_id: n.node_id,
							class_type: n.class_type,
							lora_name: n.lora_name,
							strength_model: n.strength_model
						})),
						replaced: [...loraReplacedIds],
						kept: [...loraKeptIds]
					}
				: null
		};
	}

	function slugifyImportTabId(label) {
		const slug = (label || '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase();
		return slug || 'tab';
	}

	function findImportTab(ref) {
		if (!ref) return null;
		return form.tabs.find((t) => t.id === ref) || form.tabs.find((t) => (t.label || '').toLowerCase() === String(ref).toLowerCase()) || null;
	}

	function tabIdForField(field) {
		for (const t of form.tabs) {
			if (collectFieldsFromItems(t.items, []).includes(field)) return t.id;
		}
		return null;
	}

	function mappingCandidate(nodeId, inputName) {
		if (!nodeId || !inputName) return null;
		return (analysis?.candidates || []).find((c) => c.node_id === nodeId && c.input_name === inputName) || null;
	}

	// `forField` is the field this mapping would belong to once applied -
	// `null` for a mapping on a field that doesn't exist yet (add_field: it
	// can only ever be unowned), the field object itself for a mapping onto
	// an EXISTING field (map: also fine if the candidate is already mapped to
	// that same field - re-applying isn't a conflict).
	function importMappingAvailable(nodeId, inputName, forField) {
		const candidate = mappingCandidate(nodeId, inputName);
		if (!candidate) return false;
		if (isLockedCandidate(candidate)) return false;
		const owner = mappedFieldByKey.get(candidateKey(candidate));
		return !owner || owner === forField;
	}

	// `revisionKnownCurrent` - true only when the proposal's form_revision is
	// known AND equals the CURRENT one - gates overwriting a transform an
	// existing mapping already has: requesting the transform it already has
	// is always a no-op (nothing to conflict with); requesting a DIFFERENT
	// one from an older or unknown revision is a real conflict (the user may
	// have changed it since) and is refused rather than silently overwritten.
	// A brand-new mapping (no existing entry) is unaffected by this - it's
	// additive, not an update, and applies regardless of revision as long as
	// `importMappingAvailable` allows it.
	function applyImportMapping(field, nodeId, inputName, transform, revisionKnownCurrent) {
		if (!importMappingAvailable(nodeId, inputName, field)) {
			console.warn('propose_form_changes: mapping unavailable (missing target, locked, unknown, or mapped elsewhere)', { field: field?.field_name, nodeId, inputName });
			return false;
		}
		const candidate = mappingCandidate(nodeId, inputName);
		const requestedTransform = transform && transform !== 'none' ? transform : 'none';
		const existing = field.mappings.find((m) => m.node_id === nodeId && m.input_name === inputName);
		if (existing) {
			const currentTransform = existing.transform || 'none';
			if (currentTransform === requestedTransform) return true;
			if (!revisionKnownCurrent) {
				console.warn('propose_form_changes: mapping already has a different transform set since this was proposed - not overwritten', { field: field?.field_name, nodeId, inputName, requestedTransform, currentTransform });
				return false;
			}
			setMappingTransform(field, candidate, requestedTransform);
			return true;
		}
		toggleMapping(field, candidate, true);
		if (requestedTransform !== 'none') setMappingTransform(field, candidate, requestedTransform);
		return true;
	}

	// An op naming a tab (`op.tab`) that no longer exists is never retargeted
	// onto the active tab or form.tabs[0] - that would apply it somewhere the
	// proposal never asked for. Only an op with NO tab at all falls back to
	// "wherever the user is looking right now", matching where a manual "+
	// Add" from the toolbar would land.
	function resolveOpTab(op) {
		if (op.tab) return findImportTab(op.tab);
		return activeTab || form.tabs[0] || null;
	}

	// Returns an outcome the chat host surfaces instead of a blanket success
	// toast/narration - {status: 'applied'|'stale'|'partial'|'noop', applied,
	// skipped, message?}. `draft_id` is a hard gate (a proposal built for a
	// workflow that's since been replaced/reset never touches the live form,
	// no matter how much its tab/field names happen to overlap, and a result
	// with no draft_id at all has no provable owner) - once that passes,
	// every op is validated against the CURRENT form/candidates BEFORE any
	// mutation (tab existence with no retargeting, and for add_field every
	// requested mapping's availability, all pre-checked before the field is
	// created) regardless of whether form_revision drifted, so this is
	// correct whether or not anything actually changed. The one place
	// `form_revision` gates rather than just phrases: a `map` (or an
	// add_field mapping) UPDATING a transform an existing mapping on the same
	// field already has only lands when the proposal's revision is known to
	// be current, or the requested transform already matches - a
	// same-transform "update" is always a no-op, but a genuinely different
	// one from an older/unknown revision could be overwriting a change the
	// user made after the proposal was built, so it's refused (see
	// applyImportMapping). Otherwise `form_revision` is consulted only to say
	// whether a skip happened because "the form changed since this was
	// proposed" or because the op was never valid to begin with. Only ever
	// additive against a live edit - nothing the user already changed is
	// replaced.
	function applyImportFormChanges(result) {
		const ops = result?.ops;
		if (!Array.isArray(ops) || ops.length === 0) return { status: 'noop', applied: 0, skipped: 0 };

		// Stamped by the backend tool from the wizard's own context at the
		// moment the proposal was made (see backend.chat.tools) - never
		// model-supplied, so it can't be spoofed by a hallucinated op. A
		// result with no draft_id has no provable owner and is never applied.
		const proposalDraftId = result?.draft_id != null ? String(result.draft_id) : null;
		if (proposalDraftId === null) {
			return {
				status: 'stale',
				applied: 0,
				skipped: ops.length,
				message: 'This proposal predates the current import session, so nothing was applied. Ask for a fresh proposal.'
			};
		}
		if (proposalDraftId !== draftId) {
			return {
				status: 'stale',
				applied: 0,
				skipped: ops.length,
				message: "This proposal was for a workflow that's no longer loaded here, so nothing was applied. Ask again if you still want these changes."
			};
		}

		const proposalRevision = typeof result?.form_revision === 'number' ? result.form_revision : null;
		const revisionDrifted = proposalRevision !== null && proposalRevision !== formRevision;
		// Distinct from `!revisionDrifted` - null/unknown counts as NOT known
		// current (conservative default for a caller that never sent one),
		// where `revisionDrifted` alone would treat unknown as "not drifted".
		const revisionKnownCurrent = proposalRevision !== null && !revisionDrifted;

		let lastTabId = null;
		let applied = 0;
		let skipped = 0;

		for (const op of ops) {
			if (!op || typeof op !== 'object') {
				skipped += 1;
				continue;
			}

			if (op.op === 'add_tab') {
				const label = op.label || 'Tab';
				const baseId = op.id || slugifyImportTabId(label);
				let id = baseId;
				let n = 2;
				while (form.tabs.some((t) => t.id === id)) {
					id = `${baseId}_${n}`;
					n += 1;
				}
				form.tabs.push({ id, label, icon: null, items: [] });
				lastTabId = id;
				applied += 1;
			} else if (op.op === 'add_field') {
				const tab = resolveOpTab(op);
				if (!tab) {
					console.warn('propose_form_changes: add_field target tab not found', op);
					skipped += 1;
					continue;
				}
				const mappings = op.mappings || [];
				if (!mappings.every((m) => importMappingAvailable(m.node_id, m.input_name, null))) {
					console.warn('propose_form_changes: add_field skipped - a requested mapping is no longer available', op);
					skipped += 1;
					continue;
				}
				addItemToContainer(tab.items, 'field');
				const created = tab.items[tab.items.length - 1];
				created.field_name =
					op.field_name && !allFields.some((f) => f !== created && f.field_name === op.field_name)
						? op.field_name
						: uniqueFieldName(op.field_name || 'field');
				created.field_type = op.field_type || 'text';
				created.label = op.label || created.field_name;
				if ('default' in op) created.default = op.default ?? null;
				for (const m of mappings) applyImportMapping(created, m.node_id, m.input_name, m.transform, revisionKnownCurrent);
				lastTabId = tab.id;
				applied += 1;
			} else if (op.op === 'map') {
				const field = findFieldByName(op.field_name);
				if (!field) {
					console.warn(`propose_form_changes: map references unknown field '${op.field_name}'`, op);
					skipped += 1;
					continue;
				}
				if (!applyImportMapping(field, op.node_id, op.input_name, op.transform, revisionKnownCurrent)) {
					skipped += 1;
					continue;
				}
				lastTabId = tabIdForField(field) || lastTabId;
				applied += 1;
			} else if (op.op === 'lora_picker') {
				const tab = resolveOpTab(op);
				if (!tab) {
					console.warn('propose_form_changes: lora_picker target tab not found', op);
					skipped += 1;
					continue;
				}
				const keepFixed = new Set(Array.isArray(op.keep_fixed) ? op.keep_fixed : []);
				if (applyLoraPickerConversion(tab, keepFixed, op.field_name || 'loras')) {
					lastTabId = tab.id;
					applied += 1;
				} else {
					console.warn('propose_form_changes: lora_picker could not be applied (no chain, or already converted)', op);
					skipped += 1;
				}
			} else {
				console.warn('propose_form_changes: unknown op', op);
				skipped += 1;
			}
		}

		if (applied === 0) {
			if (skipped === 0) return { status: 'noop', applied: 0, skipped: 0 };
			const message = revisionDrifted
				? `The form changed since the assistant proposed this - none of the ${skipped} change${skipped === 1 ? '' : 's'} still applied.`
				: `None of the ${skipped} proposed change${skipped === 1 ? '' : 's'} could be applied.`;
			return { status: 'stale', applied: 0, skipped, message };
		}

		if (step < 2) step = 2;
		if (lastTabId) activeTabId = lastTabId;

		if (skipped > 0) {
			const message = revisionDrifted
				? `Applied ${applied} change${applied === 1 ? '' : 's'} from the assistant - ${skipped} ${skipped === 1 ? 'was' : 'were'} skipped because the form changed since the proposal was made.`
				: `Applied ${applied} change${applied === 1 ? '' : 's'} from the assistant - ${skipped} could not be applied.`;
			return { status: 'partial', applied, skipped, message };
		}

		window.__potionui?.notifications?.toast?.('success', `Applied ${applied} change${applied === 1 ? '' : 's'} from the assistant`);
		return { status: 'applied', applied, skipped: 0 };
	}

	$effect(() => {
		const chat = window.__potionui?.chat;
		if (!chat || step < 2 || !analysis) return;
		const unregisterContext = chat.provideContext('comfyui_import', buildImportChatContext);
		const unregisterMode = chat.declareMode('comfyui-import');
		const unregisterTool = chat.onToolApplied('propose_form_changes', applyImportFormChanges);
		return () => {
			unregisterContext();
			unregisterMode();
			unregisterTool();
		};
	});

	function fieldTypeOptionsFor(current) {
		return [...new Set([current, ...fieldTypeOptions])].filter(Boolean);
	}

	function looksLikeFilename(value) {
		return typeof value === 'string' && /\.[A-Za-z0-9]{2,12}$/.test(value);
	}

	// Guess from the mapped workflow input name alone - a `model` field has
	// no node/class_type in hand at this point, only `item.mappings`.
	function guessModelTypeFromInputName(inputName) {
		if (inputName.startsWith('lora_name')) return 'lora';
		if (inputName.startsWith('ckpt_name')) return 'checkpoint';
		if (inputName.startsWith('unet_name')) return 'diffusion_model';
		if (inputName.startsWith('vae_name')) return 'vae';
		if (inputName.startsWith('clip_name')) return 'text_encoder';
		if (inputName.startsWith('control_net_name')) return 'controlnet';
		if (inputName === 'model_name') return 'upscaler';
		return 'checkpoint';
	}

	function coerceToNumber(value) {
		const n = Number(value);
		return Number.isFinite(n) ? n : 0;
	}

	function coerceToBoolean(value) {
		if (typeof value === 'string') return value.toLowerCase() === 'true' || value === '1';
		return Boolean(value);
	}

	const TEXT_FIELD_TYPES = new Set(['textbox', 'string']);

	// `field_type`s whose `default` must land as a native int/float/bool -
	// see `schema._typed_default` (backend/preset_import/schema.py), which
	// this table mirrors so the wizard never hands the backend a string it
	// would just reject.
	const INT_FIELD_TYPES = new Set(['integer', 'stepper', 'seed']);
	const NUMERIC_FIELD_TYPES = new Set(['number', 'slider']);
	const BOOL_FIELD_TYPES = new Set(['checkbox', 'boolean', 'gate']);

	function configSchemaFor(fieldType) {
		return fieldTypeManifest[fieldType]?.configuration_schema ?? [];
	}

	// Generic replacement for a hand-written per-type carry-forward table:
	// a key the new type's own `configuration_schema` also declares survives
	// with its old value, a required key with no old value gets the schema's
	// default, everything else (a `select`'s 700-entry `options` surviving
	// into a `lora_picker` as a broken hybrid) is dropped.
	function seedConfigForType(newType, prevConfig) {
		const schema = configSchemaFor(newType);
		if (!schema.length) return null;
		const prev = prevConfig || {};
		const next = {};
		for (const spec of schema) {
			if (prev[spec.name] !== undefined) next[spec.name] = prev[spec.name];
			else if (spec.required) next[spec.name] = spec.default;
		}
		return Object.keys(next).length ? next : null;
	}

	// A field's `config` (and often `default`) is shaped for its old
	// `field_type` - carrying it across a type change is how a `select`'s
	// 700-entry `options` list survives into a `lora_picker` as a broken
	// hybrid. Called from the type <select>'s onchange with what the field
	// is switching to; returns the `{ default, config }` the new type
	// actually expects. `lora_picker` and `model` stay hand-seeded: their
	// config depends on data the contract can't supply on its own (a
	// friendlier populated strength/placeholder set to show right away, and
	// a model_type guessed from the mapped workflow input name).
	function configForTypeChange(item, newType) {
		if (newType === 'lora_picker') {
			const prevName = looksLikeFilename(item.default) ? item.default : null;
			return {
				default: prevName ? [{ model: `models/loras/${prevName}`, strength: 1 }] : [],
				config: loraPickerConfig()
			};
		}
		if (newType === 'model') {
			const inputName = item.mappings?.[0]?.input_name || '';
			return {
				default: typeof item.default === 'string' ? item.default : '',
				config: { model_type: guessModelTypeFromInputName(inputName), allow_info_modal: true }
			};
		}
		if (newType === 'select') {
			const hasOptions = Array.isArray(item.config?.options);
			return { default: item.default, config: hasOptions ? item.config : seedConfigForType(newType, item.config) };
		}
		if (newType === 'slider' || newType === 'number' || newType === 'stepper') {
			return { default: coerceToNumber(item.default), config: seedConfigForType(newType, item.config) };
		}
		if (newType === 'checkbox') {
			return { default: coerceToBoolean(item.default), config: null };
		}
		const sameFamily = TEXT_FIELD_TYPES.has(newType) ? TEXT_FIELD_TYPES.has(item.field_type) : item.field_type === newType;
		return { default: typeof item.default === 'string' ? item.default : null, config: sameFamily ? item.config : seedConfigForType(newType, item.config) };
	}

	function changeFieldType(item, newType) {
		if (newType === item.field_type) return;
		const { default: nextDefault, config } = configForTypeChange(item, newType);
		item.field_type = newType;
		item.default = nextDefault;
		item.config = config;
		if (newType !== 'resolution') delete item._wh;
	}

	// ---- Field card: configuration_schema-driven editor ----

	function configDraftKey(item, spec) {
		return `${item._id}::${spec.name}`;
	}

	function configValue(item, spec) {
		const v = item.config?.[spec.name];
		return v !== undefined ? v : spec.default;
	}

	function setConfigValue(item, name, value) {
		if (!item.config) item.config = {};
		item.config[name] = value;
	}

	function clearConfigValue(item, name) {
		if (item.config) delete item.config[name];
	}

	function onNumberConfigInput(item, spec, raw) {
		const trimmed = raw.trim();
		if (trimmed === '') {
			clearConfigValue(item, spec.name);
			return;
		}
		const n = Number(trimmed);
		if (!Number.isFinite(n)) return;
		setConfigValue(item, spec.name, spec.param_type === 'int' ? Math.trunc(n) : n);
	}

	function onTextConfigInput(item, spec, raw) {
		if (raw === '') {
			clearConfigValue(item, spec.name);
			return;
		}
		setConfigValue(item, spec.name, raw);
	}

	// A select/checkbox_group/resolution `options` entry is `{label, value,
	// example?}` (see select.py) rather than a bare scalar - still "one
	// choice per line" from an editing standpoint, so the line editor
	// handles it too, just folding each typed line into `{label, value}`
	// (dropping any `example` a round-tripped entry had) instead of a
	// literal string.
	function isOptionObjectList(sample) {
		return Array.isArray(sample) && sample.length > 0 && sample.every((x) => x && typeof x === 'object' && ('value' in x || 'label' in x));
	}

	// `list`-typed config isn't always a list on the wire - `filter_tags`
	// also accepts a bare `"@config:<key>"` string (see model.py) - so a
	// current string value stays a text control rather than being forced
	// into a line/JSON editor. An empty/unset value falls back to `lines`
	// (rather than `spec.example`'s shape) unless the example is itself
	// option-object-shaped, so a field with no current value never
	// mis-defaults to opening a large JSON box.
	function listControlKind(item, spec) {
		const v = configValue(item, spec);
		if (typeof v === 'string') return 'text';
		const sample = Array.isArray(v) && v.length ? v : Array.isArray(spec.example) ? spec.example : null;
		if (!sample) return 'lines';
		if (isOptionObjectList(sample)) return 'option-lines';
		if (sample.some((x) => x !== null && typeof x === 'object')) return 'json';
		return 'lines';
	}

	// Read-only: writing to a $state store as a side effect of a template
	// `value={...}` read is a derived-context mutation Svelte 5 rejects
	// (`state_unsafe_mutation`), silently aborting that render pass - the
	// draft is seeded lazily by returning the computed fallback, never by
	// writing it back here. Only the input handlers below write drafts.
	function listDraftFor(item, spec) {
		const key = configDraftKey(item, spec);
		if (configListDrafts[key] !== undefined) return configListDrafts[key];
		const v = configValue(item, spec);
		const arr = Array.isArray(v) ? v : [];
		return arr.map((x) => (x && typeof x === 'object' ? String(x.value ?? x.label ?? '') : String(x))).join('\n');
	}

	function onListDraftInput(item, spec, raw, kind) {
		const key = configDraftKey(item, spec);
		configListDrafts[key] = raw;
		const lines = raw.split('\n').map((l) => l.trim()).filter(Boolean);
		if (lines.length === 0) {
			clearConfigValue(item, spec.name);
			return;
		}
		setConfigValue(item, spec.name, kind === 'option-lines' ? lines.map((l) => ({ label: l, value: l })) : lines);
	}

	// Read-only for the same reason as listDraftFor above.
	function jsonDraftFor(item, spec) {
		const key = configDraftKey(item, spec);
		if (configJsonDrafts[key] !== undefined) return configJsonDrafts[key];
		const v = configValue(item, spec);
		return v === undefined || v === null ? '' : JSON.stringify(v, null, 2);
	}

	function onJsonDraftInput(item, spec, raw) {
		const key = configDraftKey(item, spec);
		configJsonDrafts[key] = raw;
		const trimmed = raw.trim();
		if (trimmed === '') {
			configJsonErrors[key] = '';
			clearConfigValue(item, spec.name);
			return;
		}
		try {
			const parsed = JSON.parse(trimmed);
			configJsonErrors[key] = '';
			setConfigValue(item, spec.name, parsed);
		} catch (e) {
			configJsonErrors[key] = 'Invalid JSON';
		}
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
		const trimmed = text.trim();
		if (INT_FIELD_TYPES.has(item.field_type) || NUMERIC_FIELD_TYPES.has(item.field_type)) {
			if (trimmed === '') {
				item.default = null;
				return;
			}
			const n = Number(trimmed);
			// Unparsable, or a fraction typed into a whole-number field -
			// leave the previous default alone rather than clobbering it
			// with 0 (coerceToNumber's own fallback, right for a type
			// change, wrong for an in-place edit).
			if (!Number.isFinite(n) || (INT_FIELD_TYPES.has(item.field_type) && !Number.isInteger(n))) return;
			item.default = coerceToNumber(trimmed);
			return;
		}
		if (BOOL_FIELD_TYPES.has(item.field_type)) {
			if (trimmed === '') {
				item.default = null;
				return;
			}
			const lowered = trimmed.toLowerCase();
			if (lowered !== 'true' && lowered !== 'false' && lowered !== '1' && lowered !== '0') return;
			item.default = coerceToBoolean(lowered);
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
					const list = (payload?.data ?? []).filter((t) => !t.container);
					fieldTypeOptions = [...new Set(list.map((t) => t.type))].sort();
					fieldTypeManifest = Object.fromEntries(list.map((t) => [t.type, t]));
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

	onDestroy(() => {
		retireSource();
	});

	async function startEdit(presetId) {
		retireSource();
		const token = sourceToken;
		editPresetId = presetId;
		editLoading = true;
		analyzeError = '';
		try {
			const res = await fetch(`${API_BASE}/presets/imported/${presetId}/source`, { credentials: 'include', headers: authHeaders() });
			const payload = await res.json().catch(() => null);
			if (token !== sourceToken) return;
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
			if (token !== sourceToken) return;
			analyzeError = 'Could not reach the server.';
			editPresetId = null;
		} finally {
			if (token === sourceToken) editLoading = false;
		}
	}

	function readFile(file) {
		retireSource();
		const token = sourceToken;
		pendingFileRead = true;
		const reader = new FileReader();
		reader.onload = (e) => {
			if (token !== sourceToken) return;
			pendingFileRead = false;
			rawText = e.target?.result ?? '';
			analyzeError = '';
		};
		reader.onerror = () => {
			if (token !== sourceToken) return;
			pendingFileRead = false;
			analyzeError = 'Could not read that file - try selecting it again.';
		};
		reader.readAsText(file);
	}

	// A keystroke or paste in the source textarea is itself a source change:
	// it must retire whatever analyze/edit-load (or still-pending file read)
	// belonged to the text it is replacing, the same as picking a new file
	// does, so a stale response can neither land on top of what's now on
	// screen nor leave the UI stuck mid-loading.
	function handleSourceTextInput() {
		retireSource();
		analyzeError = '';
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
		// Belt-and-braces alongside the button's own `disabled` - a picked
		// file that hasn't finished reading owns the source; the textarea's
		// current value belongs to whatever was retired when it was picked
		// and must not be analyzed out from under the read that's replacing
		// it.
		if (pendingFileRead) return;
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
		const token = sourceToken;
		analyzing = true;
		try {
			const res = await fetch(`${API_BASE}/presets/import/analyze`, {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json', ...authHeaders() },
				body: JSON.stringify({ workflow: parsed })
			});
			const payload = await res.json().catch(() => null);
			if (token !== sourceToken) return;
			if (!res.ok) {
				analyzeError = payload?.detail || payload?.message || `Analyze failed (${res.status})`;
				return;
			}
			workflowJson = parsed;
			analysis = payload;
			initializeFormAndHistory(payload);
			step = 2;
		} catch (e) {
			if (token !== sourceToken) return;
			analyzeError = 'Could not reach the server.';
		} finally {
			if (token === sourceToken) analyzing = false;
		}
	}

	function changeWorkflow() {
		retireSource();
		step = 1;
		form = emptyForm();
		activeTabId = 'generation';
		historyRows = [];
		historyBuilt = false;
		initialHistoryDefault = [];
		requirementsError = '';
		requirementsResults = null;
		createResult = null;
		createError = '';
	}

	async function goToRequirementsStep() {
		step = 4;
		const ownsCurrent = requirementsToken === sourceToken;
		if (ownsCurrent && (requirementsResults || requirementsLoading)) return;
		await runRequirementsPreview();
	}

	async function runRequirementsPreview() {
		const token = sourceToken;
		requirementsToken = token;
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
			if (token !== sourceToken) return;
			if (!res.ok) {
				requirementsError = payload?.detail || payload?.message || `Requirements check failed (${res.status})`;
				return;
			}
			requirementsResults = payload.results ?? [];
		} catch (e) {
			if (token !== sourceToken) return;
			requirementsError = 'Could not reach the server.';
		} finally {
			if (token === sourceToken) requirementsLoading = false;
		}
	}

	function historyPayload() {
		return historyRows
			.filter((r) => r.enabled)
			.map((r) => ({ field: r.field_name, label: r.label, format: r.format, template: r.format === 'jinja' ? r.template || '' : null }));
	}

	async function runCreate() {
		if (!analysis || !workflowJson || creating) return;
		// The server-side create must never be replayed, cancelled or undone
		// because the view moved on (Import another / teardown) while it was
		// in flight - only whether its result gets applied/announced is
		// gated on still owning this source when the response lands.
		const token = sourceToken;
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
					history: historyPayload(),
					// Echoed from analyze/source's own response so the server can
					// refuse (400) rather than silently re-save under a different
					// classification if the workflow's node schema drifted since -
					// see backend/preset_import/emit._check_schema_drift.
					schema_fingerprint: analysis.schema_fingerprint,
					schema_object_info_used: analysis.object_info_used
				})
			});
			const payload = await res.json().catch(() => null);
			if (token !== sourceToken) return;
			if (!res.ok) {
				createError = payload?.detail || payload?.message || `Import failed (${res.status})`;
				return;
			}
			createResult = payload;
			step = 5;
		} catch (e) {
			if (token !== sourceToken) return;
			createError = 'Could not reach the server.';
		} finally {
			if (token === sourceToken) creating = false;
		}
	}

	function goBack() {
		if (step > 1) step -= 1;
	}

	function importAnother() {
		retireSource();
		step = 1;
		editPresetId = null;
		rawText = '';
		analyzeError = '';
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
		requirementsError = '';
		requirementsResults = null;
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

{#snippet tabIcon(name, size = 13)}
	<svg class="icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"><use href={`#ti-${name}`}></use></svg>
{/snippet}

{#snippet addMenu(items, isRoot = false)}
	<div class="di-add-wrap" data-add-root={isRoot ? 'true' : undefined}>
		<button type="button" class="di-add-field" onclick={(e) => toggleAddMenu(items, e.currentTarget)} data-action="open-add-menu">
			{@render icon('plus')} Add
		</button>
		{#if addMenuOpenFor === items}
			<div class="di-add-menu" use:floating={{ anchor: addMenuAnchorEl, onOutsideClick: () => (addMenuOpenFor = null) }}>
				<button type="button" onclick={() => addItemToContainer(items, 'field')} data-add-kind="field">{@render icon('arrow-right')}Field<span class="type-tag">input</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'row')} data-add-kind="row">{@render icon('columns')}Row<span class="type-tag">layout</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'group')} data-add-kind="group">{@render icon('folder')}Group<span class="type-tag">layout</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'section')} data-add-kind="section">{@render icon('layers')}Section<span class="type-tag">section</span></button>
				<button type="button" onclick={() => addItemToContainer(items, 'header')} data-add-kind="header">{@render icon('heading')}Header<span class="type-tag">display</span></button>
			</div>
		{/if}
	</div>
{/snippet}

{#snippet moveToMenu(item, parentItems, index)}
	{#if form.tabs.length > 1}
		<button type="button" class="iconbtn" title="Move to tab" onclick={(e) => toggleMoveToPopover(item._id, e.currentTarget)} data-action="move-to-tab-menu">
			{@render icon('more')}
		</button>
		{#if moveToPopoverId === item._id}
			<div
				class="tab-popover"
				use:floating={{ anchor: moveToPopoverAnchorEl, onOutsideClick: () => (moveToPopoverId = null) }}
				onclick={(e) => e.stopPropagation()}
				onkeydown={(e) => e.stopPropagation()}
			>
				<div class="popover-label">Move to</div>
				{#each form.tabs.filter((t) => t.id !== activeTabId) as t (t.id)}
					<button
						type="button"
						onclick={() => { moveItemToTab(parentItems, index, t.id); moveToPopoverId = null; }}
						data-action="move-to-tab"
						data-target-tab={t.id}
					>
						{t.label}
					</button>
				{/each}
			</div>
		{/if}
	{/if}
{/snippet}

{#snippet configControl(item, spec)}
	{#if spec.param_type === 'int' || spec.param_type === 'float'}
		<input
			type="number"
			step={spec.param_type === 'int' ? 1 : 'any'}
			class="di-config-input"
			placeholder={spec.default ?? ''}
			value={configValue(item, spec) ?? ''}
			oninput={(e) => onNumberConfigInput(item, spec, e.currentTarget.value)}
			aria-label={spec.name}
		/>
	{:else if spec.param_type === 'bool'}
		<input
			type="checkbox"
			checked={Boolean(configValue(item, spec))}
			onchange={(e) => setConfigValue(item, spec.name, e.currentTarget.checked)}
			aria-label={spec.name}
		/>
	{:else if spec.param_type === 'str' && spec.choices?.length}
		<select class="di-config-input" value={configValue(item, spec) ?? ''} onchange={(e) => setConfigValue(item, spec.name, e.currentTarget.value)} aria-label={spec.name}>
			{#each spec.choices as c}<option value={c}>{c}</option>{/each}
		</select>
	{:else if spec.param_type === 'dict'}
		<textarea
			class="di-config-json"
			rows="3"
			value={jsonDraftFor(item, spec)}
			oninput={(e) => onJsonDraftInput(item, spec, e.currentTarget.value)}
			aria-label={spec.name}
		></textarea>
		{#if configJsonErrors[configDraftKey(item, spec)]}<div class="di-config-error">{configJsonErrors[configDraftKey(item, spec)]}</div>{/if}
	{:else if spec.param_type === 'list'}
		{#if listControlKind(item, spec) === 'json'}
			<textarea
				class="di-config-json"
				rows="3"
				value={jsonDraftFor(item, spec)}
				oninput={(e) => onJsonDraftInput(item, spec, e.currentTarget.value)}
				aria-label={spec.name}
			></textarea>
			{#if configJsonErrors[configDraftKey(item, spec)]}<div class="di-config-error">{configJsonErrors[configDraftKey(item, spec)]}</div>{/if}
		{:else if listControlKind(item, spec) === 'text'}
			<input type="text" class="di-config-input" value={configValue(item, spec) ?? ''} oninput={(e) => onTextConfigInput(item, spec, e.currentTarget.value)} aria-label={spec.name} />
		{:else}
			{@const kind = listControlKind(item, spec)}
			<textarea
				class="di-config-lines"
				rows="3"
				placeholder="One entry per line"
				value={listDraftFor(item, spec)}
				oninput={(e) => onListDraftInput(item, spec, e.currentTarget.value, kind)}
				aria-label={spec.name}
			></textarea>
		{/if}
	{:else}
		<input
			type="text"
			class="di-config-input"
			placeholder={spec.default ?? ''}
			value={configValue(item, spec) ?? ''}
			oninput={(e) => onTextConfigInput(item, spec, e.currentTarget.value)}
			aria-label={spec.name}
		/>
	{/if}
{/snippet}

{#snippet fieldCard(item, parentItems, index)}
	<div
		class="di-field-card"
		data-field-name={item.field_name}
	>
		<div class="di-field-top">
			<span class="drag-handle" title="Drag to move to another tab" draggable="true" ondragstart={(e) => handleItemDragStart(e, item._id)} ondragend={handleItemDragEnd}>{@render icon('grip')}</span>
			<input class="di-field-label" type="text" bind:value={item.label} aria-label="Field label" />
			<select class="di-field-type" value={item.field_type} onchange={(e) => changeFieldType(item, e.currentTarget.value)} aria-label="Field type">
				{#each fieldTypeOptionsFor(item.field_type) as opt}<option value={opt}>{opt}</option>{/each}
			</select>
			<input
				class="di-field-default"
				type="text"
				inputmode={INT_FIELD_TYPES.has(item.field_type) || NUMERIC_FIELD_TYPES.has(item.field_type) ? 'decimal' : undefined}
				value={displayDefault(item)}
				oninput={(e) => setDefaultFromText(item, e.currentTarget.value)}
				aria-label="Default value"
			/>
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
					{@render icon('link')}
				</button>
				<button type="button" class="iconbtn" title="Move up" onclick={() => moveItemAt(parentItems, index, -1)} data-action="move-up">{@render icon('chevron-up')}</button>
				<button type="button" class="iconbtn" title="Move down" onclick={() => moveItemAt(parentItems, index, 1)} data-action="move-down">{@render icon('chevron-down')}</button>
				{@render moveToMenu(item, parentItems, index)}
				<button type="button" class="iconbtn" title="Remove" onclick={() => removeItemAt(parentItems, index)} data-action="remove">{@render icon('x')}</button>
			</div>
		</div>
		{#if item.mappings.length > 0}
			<div class="di-mapping"><span class="line mono"><span class="arrow">→</span>{item.mappings.map((m) => `${m.node_id}.inputs.${m.input_name}`).join(' · ')}</span></div>
		{:else if !dismissedSuggestionIds[item._id]}
			{@const suggestions = nameMatchSuggestionsFor(item)}
			{#if suggestions.length > 0}
				<div class="di-suggest" data-name-suggestions>
					<div class="di-suggest-rows">
						{#each suggestions as c (candidateKey(c))}
							<div class="di-suggest-row">
								<span class="di-suggest-text">Matches workflow input <span class="mono">{c.node_id}.inputs.{c.input_name}</span></span>
								<button type="button" class="di-suggest-map" onclick={() => toggleMapping(item, c, true)} data-action="apply-name-match">Map</button>
							</div>
						{/each}
					</div>
					<button type="button" class="di-suggest-dismiss" onclick={() => dismissSuggestion(item._id)} aria-label="Dismiss suggestion" data-action="dismiss-name-match">{@render icon('x', 10)}</button>
				</div>
			{/if}
		{/if}
		{#if expandedFieldId === item._id}
			{@const configSpecs = configSchemaFor(item.field_type)}
			{@const sortedMapCandidates = [...mappableCandidates].sort((a, b) => Number(candidateNameMatchesField(b, item)) - Number(candidateNameMatchesField(a, item)))}
			{#if configSpecs.length > 0}
				<div class="di-config" data-config-editor>
					<div class="di-mapedit-label">Configuration</div>
					<div class="di-config-list">
						{#each configSpecs as spec (spec.name)}
							<div class="di-config-row" data-config-row={spec.name}>
								<div class="di-config-head">
									<span class="di-config-name mono">{spec.name}</span>
									{#if spec.required}<span class="chip chip-warn">required</span>{/if}
								</div>
								<div class="di-config-control">
									{@render configControl(item, spec)}
								</div>
								{#if spec.description}<div class="di-config-desc">{spec.description}</div>{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}
			<div class="di-mapedit" data-mapping-editor>
				<div class="di-mapedit-label">Mapped workflow inputs</div>
				<div class="di-mapedit-list">
					{#each sortedMapCandidates as c (candidateKey(c))}
						{@const checked = item.mappings.some((m) => m.node_id === c.node_id && m.input_name === c.input_name)}
						{@const mappedElsewhere = mappedFieldByKey.get(candidateKey(c)) && mappedFieldByKey.get(candidateKey(c)) !== item}
						{@const isNameMatch = candidateNameMatchesField(c, item)}
						<div class="di-mapedit-row" class:selected={checked} data-mapedit-key={candidateKey(c)}>
							<input type="checkbox" {checked} disabled={mappedElsewhere} onchange={(e) => toggleMapping(item, c, e.currentTarget.checked)} />
							<span class="di-mapedit-node mono">{c.node_id} · {c.class_type}</span>
							<span class="di-mapedit-input mono">{c.input_name}</span>
							{#if isNameMatch}<span class="chip chip-info" data-name-match-chip>name match</span>{/if}
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
	<div
		class="di-header-card"
	>
		<span class="drag-handle" title="Drag to move to another tab" draggable="true" ondragstart={(e) => handleItemDragStart(e, item._id)} ondragend={handleItemDragEnd}>{@render icon('grip')}</span>
		<span class="chip chip-warn">HEADER</span>
		<input class="di-header-input" type="text" bind:value={item.text} aria-label="Header text" />
		<div class="di-container-actions">
			<button type="button" class="iconbtn" title="Move up" onclick={() => moveItemAt(parentItems, index, -1)} data-action="move-up">{@render icon('chevron-up')}</button>
			<button type="button" class="iconbtn" title="Move down" onclick={() => moveItemAt(parentItems, index, 1)} data-action="move-down">{@render icon('chevron-down')}</button>
			{@render moveToMenu(item, parentItems, index)}
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
	<div
		class="di-container"
		class:collapsed={item.kind === 'section' && item.collapsed}
		data-item-kind={item.kind}
	>
		<div class="di-container-head">
			<span class="drag-handle" title="Drag to move to another tab" draggable="true" ondragstart={(e) => handleItemDragStart(e, item._id)} ondragend={handleItemDragEnd}>{@render icon('grip')}</span>
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
				{@render moveToMenu(item, parentItems, index)}
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
		<symbol id="i-link" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6" fill="currentColor" /><circle cx="12" cy="12" r="1.6" fill="currentColor" /><circle cx="19" cy="12" r="1.6" fill="currentColor" /></symbol>
		<symbol id="i-plus" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></symbol>
		<symbol id="i-pencil" viewBox="0 0 24 24"><path d="M12 20h9" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="i-columns" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="16" rx="1" fill="none" stroke="currentColor" stroke-width="2" /><rect x="14" y="4" width="7" height="16" rx="1" fill="none" stroke="currentColor" stroke-width="2" /></symbol>
		<symbol id="i-folder" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></symbol>
		<symbol id="i-layers" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /><polyline points="2 17 12 22 22 17" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /><polyline points="2 12 12 17 22 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></symbol>
		<symbol id="i-heading" viewBox="0 0 24 24"><path d="M6 4v16M18 4v16M6 12h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></symbol>
		<symbol id="i-inbox" viewBox="0 0 24 24"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></symbol>
		<symbol id="i-sliders" viewBox="0 0 24 24"><line x1="4" y1="21" x2="4" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="4" y1="10" x2="4" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="12" y1="21" x2="12" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="12" y1="8" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="20" y1="21" x2="20" y2="16" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="20" y1="12" x2="20" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="1" y1="14" x2="7" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="9" y1="8" x2="15" y2="8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /><line x1="17" y1="16" x2="23" y2="16" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></symbol>

		<!-- Tab-icon picker set, mirroring frontend/src/lib/utils/IconLibrary.ts's
		     paths under a `ti-` id namespace (kept separate from the `i-` UI-chrome
		     set above, which already has an unrelated glyph named "sliders") so
		     the designer's preview matches the core TabsField render exactly. -->
		<symbol id="ti-generation" viewBox="0 0 24 24"><path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-settings" viewBox="0 0 24 24"><path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-lora" viewBox="0 0 24 24"><path d="m21 7.5-2.25-1.313M21 7.5v2.25m0-2.25-2.25 1.313M3 7.5l2.25-1.313M3 7.5l2.25 1.313M3 7.5v2.25m9 3 2.25-1.313M12 12.75l-2.25-1.313M12 12.75V15m0 6.75 2.25-1.313M12 21.75V19.5m0 2.25-2.25-1.313m0-16.875L12 2.25l2.25 1.313M21 14.25v2.25l-2.25 1.313m-13.5 0L3 16.5v-2.25" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-model" viewBox="0 0 24 24"><path d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-image" viewBox="0 0 24 24"><path d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-video" viewBox="0 0 24 24"><path d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-film" viewBox="0 0 24 24"><path d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-sparkles" viewBox="0 0 24 24"><path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-embedding" viewBox="0 0 24 24"><path d="M7.5 7.5h-.75A2.25 2.25 0 0 0 4.5 9.75v7.5a2.25 2.25 0 0 0 2.25 2.25h7.5a2.25 2.25 0 0 0 2.25-2.25v-7.5a2.25 2.25 0 0 0-2.25-2.25h-.75m-6 3.75 3 3m0 0 3-3m-3 3V1.5m6 9h.75a2.25 2.25 0 0 1 2.25 2.25v7.5a2.25 2.25 0 0 1-2.25 2.25h-7.5a2.25 2.25 0 0 1-2.25-2.25v-.75" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-sliders" viewBox="0 0 24 24"><path d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-face" viewBox="0 0 24 24"><path d="M15.182 15.182a4.5 4.5 0 0 1-6.364 0M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM9.75 9.75c0 .414-.168.75-.375.75S9 10.164 9 9.75 9.168 9 9.375 9s.375.336.375.75Zm-.375 0h.008v.015h-.008V9.75Zm5.625 0c0 .414-.168.75-.375.75s-.375-.336-.375-.75.168-.75.375-.75.375.336.375.75Zm-.375 0h.008v.015h-.008V9.75Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-warning" viewBox="0 0 24 24"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
		<symbol id="ti-information-circle" viewBox="0 0 24 24"><path d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></symbol>
	</defs>
</svg>

<div class="wizard" data-import-wizard>
	<div class="wiz-rail">
		<div class="wiz-step {stepState(1)}" data-wiz-step="source">
			<div class="wiz-num">{@render stepDot(1)}</div>
			<div class="wiz-text">
				<div class="wiz-label">Source</div>
				{#if analysis}
					<div class="wiz-sub mono">export (api) · {analysis.node_count} nodes</div>
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
					<p class="desc">Paste or drop a ComfyUI Export (API) workflow JSON. In ComfyUI, enable Dev mode options in settings, then use Workflow → Export (API).</p>

					<div class="dropzone" class:dragover={dragOver} ondrop={handleDrop} ondragover={handleDragOver} ondragleave={handleDragLeave} role="group" aria-label="Workflow JSON">
						<textarea
							rows="12"
							placeholder={'{\n  "3": { "class_type": "KSampler", "inputs": { ... } },\n  ...\n}'}
							bind:value={rawText}
							oninput={handleSourceTextInput}
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
					<span class="strip-end chat-hint-group">
						{#if typeof window !== 'undefined' && window.__potionui?.chat}
							<span class="dim" data-import-chat-hint>Ask the assistant to map inputs</span>
						{/if}
						<button type="button" class="link-btn" onclick={changeWorkflow}>Change workflow</button>
					</span>
				</div>

				<div class="designer">
					<div class="di-left" data-import-form-inputs>
						<div class="di-left-title">Workflow inputs</div>
						{#if loraChainNodes.length}
							<div class="lora-chain-card" data-import-lora-chain>
								<div class="di-group-h">LoRA chain</div>
								{#each loraChainNodes as n (n.node_id)}
									{@const isKept = loraConverted ? loraKeptIds.has(n.node_id) : loraKeepFixed.has(n.node_id)}
									{@const isReplaced = loraConverted && loraReplacedIds.has(n.node_id)}
									<div class="di-row" data-lora-chain-node={n.node_id}>
										<span class="di-row-name mono">{n.node_id}</span>
										<span class="di-row-value mono">{n.lora_name}</span>
										<span class="dim mono">{n.strength_model}</span>
										{#if isReplaced}
											<span class="chip chip-info">→ picker</span>
										{/if}
										<div class="di-row-trail">
											<button
												type="button"
												class="iconbtn"
												class:active={isKept}
												title={isKept ? 'Keep fixed (won’t become part of the picker)' : 'Keep fixed'}
												disabled={loraConverted}
												onclick={() => toggleLoraKeepFixed(n.node_id)}
												data-action="lora-keep-fixed"
											>
												{@render icon('lock', 13)}
											</button>
										</div>
									</div>
								{/each}
								{#if loraSandwichError}
									<p class="message message-error" data-lora-sandwich-error>
										Kept LoRA node {loraSandwichError.nodeId} sits between replaced nodes {loraSandwichError.beforeId} and {loraSandwichError.afterId};
										keep all LoRAs above it fixed too, or replace it.
									</p>
								{/if}
								{#if loraAllKept}
									<p class="lora-chain-help" data-lora-all-kept-hint>Picker LoRAs are added after the kept node(s)</p>
								{/if}
								<div class="lora-chain-actions">
									<button
										type="button"
										class="link-btn"
										onclick={convertLoraChainToPicker}
										disabled={loraConverted || !!loraSandwichError}
										data-action="convert-lora-picker"
									>
										{loraConverted ? 'Converted to LoRA picker' : 'Convert to LoRA picker'}
									</button>
								</div>
							</div>
						{:else if modelChainInfo}
							<div class="lora-chain-card" data-import-lora-chain data-import-lora-chain-empty>
								<div class="di-group-h">LoRA chain</div>
								<p class="dim lora-chain-empty-label">No LoRA nodes detected</p>
								<p class="lora-chain-help" data-lora-no-chain-hint>Inserted before {modelChainInsertionLabel}</p>
								<div class="lora-chain-actions">
									<button
										type="button"
										class="link-btn"
										onclick={addLoraPickerNoChain}
										disabled={loraConverted}
										data-action="add-lora-picker"
									>
										{loraConverted ? 'Added LoRA picker' : 'Add LoRA picker'}
									</button>
								</div>
							</div>
						{/if}
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
									class:drop-target={dragOverTabId === tab.id}
									role="tab"
									tabindex="0"
									aria-selected={tab.id === activeTabId}
									onclick={() => (activeTabId = tab.id)}
									onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activeTabId = tab.id; } }}
									ondragover={(e) => handleTabDragOver(e, tab.id)}
									ondragleave={() => handleTabDragLeave(tab.id)}
									ondrop={(e) => handleTabDrop(e, tab.id)}
									data-tab-id={tab.id}
								>
									{#if tab.icon}
										{@render tabIcon(tab.icon)}
									{/if}
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
										onclick={(e) => { e.stopPropagation(); toggleTabPopover(tab.id, e.currentTarget); }}
										onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggleTabPopover(tab.id, e.currentTarget); } }}
										data-action="tab-menu"
									>
										{@render icon('more', 11)}
									</span>
									{#if tabPopoverId === tab.id}
										<div
											class="tab-popover"
											use:floating={{ anchor: tabPopoverAnchorEl, onOutsideClick: () => (tabPopoverId = null) }}
											onclick={(e) => e.stopPropagation()}
											onkeydown={(e) => e.stopPropagation()}
										>
											<button type="button" onclick={() => { renamingTabId = tab.id; tabPopoverId = null; }} data-action="rename-tab">{@render icon('pencil')}Rename</button>
											<button type="button" disabled={ti === 0} onclick={() => { moveTab(ti, -1); tabPopoverId = null; }} data-action="move-tab-left">{@render icon('arrow-left')}Move left</button>
											<button type="button" disabled={ti === form.tabs.length - 1} onclick={() => { moveTab(ti, 1); tabPopoverId = null; }} data-action="move-tab-right">{@render icon('arrow-right')}Move right</button>
											<hr />
											<div class="popover-label">Icon</div>
											<select
												class="tab-popover-select"
												value={tab.icon ?? ''}
												onchange={(e) => setTabIcon(tab, e.currentTarget.value)}
												data-action="tab-icon"
											>
												{#each TAB_ICON_OPTIONS as opt}
													<option value={opt.value}>{opt.label}</option>
												{/each}
											</select>
											<div class="popover-label">Display</div>
											<select
												class="tab-popover-select"
												value={tab.icon_display}
												disabled={!tab.icon}
												onchange={(e) => setTabDisplay(tab, e.currentTarget.value)}
												data-action="tab-display"
											>
												{#each TAB_DISPLAY_OPTIONS as opt}
													<option value={opt.value}>{opt.label}</option>
												{/each}
											</select>
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
					<button type="button" class="btn btn-primary" disabled={analyzing || editLoading || pendingFileRead || (!(analysis && workflowJson) && !rawText.trim())} onclick={handleSourceContinue} data-import-analyze>
						{analyzing ? 'Analyzing…' : pendingFileRead ? 'Reading file…' : 'Continue'}
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
	.chat-hint-group {
		display: flex;
		align-items: center;
		gap: 8px;
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

	.drag-handle {
		display: inline-flex;
		cursor: grab;
		flex-shrink: 0;
	}
	.drag-handle:active {
		cursor: grabbing;
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
	.lora-chain-card {
		border-bottom: 1px solid rgb(var(--line, 36 38 44));
	}
	.lora-chain-actions {
		padding: 8px 12px;
		border-top: 1px solid rgb(var(--line, 36 38 44) / 0.5);
	}
	.lora-chain-empty-label {
		padding: 0 12px;
		font-size: 12px;
	}
	.lora-chain-help {
		padding: 4px 12px 8px;
		font-size: 11.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
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
	.di-tab.drop-target {
		box-shadow: inset 0 0 0 1px rgb(var(--signal, 91 157 255));
		background: rgb(var(--signal, 91 157 255) / 0.08);
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

	.di-suggest {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		margin-top: 6px;
		padding: 6px 8px 6px 24px;
	}
	.di-suggest-rows {
		display: flex;
		flex-direction: column;
		gap: 4px;
		flex: 1;
		min-width: 0;
	}
	.di-suggest-row {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.di-suggest-text {
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.di-suggest-map {
		flex-shrink: 0;
		height: 20px;
		padding: 0 8px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 10.5px;
		font-weight: 600;
		cursor: pointer;
	}
	.di-suggest-map:hover {
		border-color: rgb(var(--line-hover, 58 62 70));
		background: rgb(var(--surface-3, 39 42 49));
	}
	.di-suggest-dismiss {
		flex-shrink: 0;
		width: 18px;
		height: 18px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border-radius: 4px;
		color: rgb(var(--fg-disabled, 92 98 112));
		background: none;
		border: none;
		cursor: pointer;
	}
	.di-suggest-dismiss:hover {
		color: rgb(var(--fg-muted, 169 174 184));
		background: rgb(var(--surface-3, 39 42 49));
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
		/* Positioned by the `floating` action (position: fixed, inline top/left) -
		   these are just its pre-mount fallback. */
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		width: 168px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		border-radius: 8px;
		padding: 4px;
		background: rgb(var(--surface-1, 22 24 28));
		box-shadow: var(--shadow-floating, 0 4px 16px rgb(0 0 0 / 0.5));
		z-index: 1000;
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

	.di-config {
		margin-top: 8px;
		padding-top: 10px;
		border-top: 1px dashed rgb(var(--line, 36 38 44));
	}
	.di-config-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.di-config-row {
		border: 1px solid rgb(var(--line, 36 38 44));
		border-radius: 4px;
		padding: 6px 8px;
	}
	.di-config-head {
		display: flex;
		align-items: center;
		gap: 6px;
		margin-bottom: 4px;
	}
	.di-config-name {
		font-size: 11px;
		color: rgb(var(--fg, 232 234 237));
	}
	.di-config-control {
		display: flex;
	}
	.di-config-input,
	.di-config-json,
	.di-config-lines {
		box-sizing: border-box;
		width: 100%;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11px;
		padding: 0 8px;
	}
	.di-config-input {
		height: 26px;
	}
	.di-config-json,
	.di-config-lines {
		padding: 6px 8px;
		font-family: inherit;
		resize: vertical;
	}
	.di-config-json {
		font-family: ui-monospace, monospace;
		font-size: 10.5px;
	}
	.di-config-desc {
		margin-top: 4px;
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
	}
	.di-config-error {
		margin-top: 4px;
		font-size: 10.5px;
		color: rgb(var(--danger, 255 138 138));
	}

	.tab-popover {
		/* Positioned by the `floating` action (position: fixed, inline top/left) -
		   these are just its pre-mount fallback. */
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		width: 168px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		border-radius: 8px;
		padding: 4px;
		background: rgb(var(--surface-1, 22 24 28));
		box-shadow: var(--shadow-floating, 0 4px 16px rgb(0 0 0 / 0.5));
		z-index: 1000;
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
	.tab-popover .popover-label {
		font-size: 10.5px;
		color: rgb(var(--fg-subtle, 122 128 144));
		padding: 4px 8px 2px;
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
	.tab-popover-select {
		box-sizing: border-box;
		width: 100%;
		height: 24px;
		margin: 0 0 4px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong, 43 46 53));
		background: rgb(var(--surface-2, 31 33 38));
		color: rgb(var(--fg, 232 234 237));
		font-size: 11px;
		padding: 0 6px;
	}
	.tab-popover-select:disabled {
		opacity: 0.5;
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
