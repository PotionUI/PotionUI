<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onDestroy, setContext } from 'svelte';
	import { writable } from 'svelte/store';
	import FormField from './form-fields/FormField.svelte';
	import { type FieldConfig, extractAllFields } from '$lib/form/reactions';
	import { valuesEqual } from './dynamicFormReactionApply';
	import { flattenFormData, hydrateFormData, settleFormReactions } from '$lib/form/formState';
	import { checkSlotValue, loadPresetFormSchema, searchAutopickSlot } from '$lib/form/formSources';
	import { toasts } from '$lib/stores/toast';
	import { createLatestRequestGuard } from '$lib/form/schemaCache';
	import { formAudienceStore } from '$lib/stores/formAudience';
	import { applyAudienceVisibilityToSchema, type FormAudience } from '$lib/utils/audienceFilter';
	import { FORM_FIELD_ERRORS_CONTEXT_KEY } from '$lib/form/fieldErrorsContext';
	import {
		FORM_FIELD_ERROR_ACTIONS_CONTEXT_KEY,
		type FormFieldErrorActions
	} from '$lib/form/fieldErrorActionsContext';
	import { ACTIVE_TAB_ID_CONTEXT_KEY } from '$lib/form/activeTabContext';
	import {
		SECTION_COLLAPSED_CONTEXT_KEY,
		type SectionCollapsedContext
	} from '$lib/form/sectionCollapsedContext';
	import { shouldPublishFormData } from '$lib/utils/formDataPublication';
	import { cloudModelId, collectCapabilityModelFields } from '$lib/form/capabilityBinder';
	import { createCapabilityTracker, sharedCapabilityCache } from '$lib/form/capabilityTracker';
	import { fetchCloudCapabilities } from '$lib/services/cloudCapabilities';
	import { clearFormulaDeclarations } from '$lib/stores/formulas';
	import { clearMissingModel, missingRequiredModelField, publishMissingModel } from '$lib/generation/requiredModelField';
	import { publishCompareSchema } from '$lib/generation/compare/compareStore.svelte';
	import { compareSchemaSignature, schemaForCompare } from '$lib/generation/compare/compareSchema';
	import {
		autopickValue,
		clearedSlotValue,
		collectAutopickSlots,
		createModelSlotAutopick,
		hasModelValue,
		isSlotClearEdit,
		markSlotCleared,
		modelWire,
		unavailableNotice
	} from '$lib/form/modelSlotAutopick';

	const PUBLISHED_SNAPSHOT_LIMIT = 8;

	// Props
	export let presetId: string;
	// The generation tab this form belongs to, if any — lets fields (e.g.
	// lora_picker) scope cross-field state to their own tab. See
	// activeTabContext.ts for why this is context rather than a prop threaded
	// through every field container.
	export let tabId: string | undefined = undefined;
	setContext(ACTIVE_TAB_ID_CONTEXT_KEY, tabId);
	// Backs SectionField.svelte's persisted fold state. Optional - callers
	// outside the generate page (or anywhere the caller has nothing to persist
	// to) simply don't pass it, and sections fall back to local-only fold
	// state. See sectionCollapsedContext.ts.
	export let sectionCollapsedContext: SectionCollapsedContext | undefined = undefined;
	setContext(SECTION_COLLAPSED_CONTEXT_KEY, sectionCollapsedContext);
	export let mode: string = 'txt2img';
	// Display-only label for the form section (aria-label). NOT sent to the backend —
	// see `variant` below for the actual form_name selector.
	export let formName: string = 'generation_form';
	// Selects the mode's preset variant (the backend's `form_name` query param).
	// `undefined` for presets/modes with a single implicit variant - the backend
	// resolves the mode's default form itself when no form_name is given, so this
	// must stay unset rather than default to a guessed name (an unmatched form_name
	// 404s server-side).
	export let variant: string | undefined = undefined;
	export let initialData: Record<string, any> = {};
	export let reloadGeneration = 0;
	// Whether the Video Director editor owns the active preset mode - hides
	// any field marked `hidden_when_video_director: true` (see
	// `$lib/utils/audienceFilter.ts`). Callers with no Director concept
	// simply don't pass it.
	export let videoDirectorActive = false;
	export let audience: FormAudience | undefined = undefined;
	export let onFormDataChange: ((data: Record<string, any>) => void) | null = null;
	// Per-field server-side validation errors (from a 422 `form_validation_failed`
	// response to `POST /api/generations/start`), keyed by field name. Rendered under
	// the offending field (see FormField.svelte) and as a per-tab count badge (see
	// TabsField.svelte) via Svelte context rather than prop-drilling through every
	// row/group/accordion/tabs container.
	export let fieldErrors: Record<string, string[]> = {};
	// Called whenever the user edits a field, so the parent can clear that field's
	// entry from `fieldErrors` (errors shouldn't linger past the next edit).
	export let onFieldEdit: ((fieldName: string) => void) | null = null;
	// Called whenever the active preset/mode/variant changes (i.e. a schema reload is
	// about to happen) so the parent can drop any stale fieldErrors for this form.
	export let onSchemaKeyChange: (() => void) | null = null;

	const fieldErrorsStore = writable<Record<string, string[]>>(fieldErrors);
	setContext(FORM_FIELD_ERRORS_CONTEXT_KEY, fieldErrorsStore);
	const fieldErrorActions: FormFieldErrorActions = {
		clearFields(names) {
			for (const name of names) onFieldEdit?.(name);
		},
		setFieldValue(name, value) {
			handleFieldChange(name, value);
		}
	};
	setContext(FORM_FIELD_ERROR_ACTIONS_CONTEXT_KEY, fieldErrorActions);
	$: fieldErrorsStore.set(fieldErrors || {});

	const MAX_REACTION_ROUNDS = 8;
	let reactionNonConvergenceWarned = false;

	// State
	let formSchema: any = null;
	let formData: Record<string, any> = {};
	let schemaLoading = true;
	let schemaError = '';
	let initialLoadComplete = false;
	const schemaRequest = createLatestRequestGuard();
	const SCHEMA_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];
	let schemaRetryCount = 0;
	let schemaRetryTimer: ReturnType<typeof setTimeout> | undefined;

	function cancelSchemaRetry() {
		clearTimeout(schemaRetryTimer);
		schemaRetryTimer = undefined;
	}

	function scheduleSchemaRetry() {
		cancelSchemaRetry();
		if (schemaRetryCount >= SCHEMA_RETRY_DELAYS_MS.length) return;
		const delay = SCHEMA_RETRY_DELAYS_MS[schemaRetryCount++];
		schemaRetryTimer = setTimeout(() => {
			schemaRetryTimer = undefined;
			if (!formSchema) void loadFormSchema();
		}, delay);
	}
	let capabilityRevision = 0;
	let capabilityHiddenNames: Set<string> = new Set();
	const capabilityTracker = createCapabilityTracker({
		fetch: fetchCloudCapabilities,
		cache: sharedCapabilityCache,
		onLoaded() {
			capabilityRevision += 1;
		}
	});

	// Track previous preset/mode to avoid unnecessary reloads
	let previousKey = '';

	// Track previous initialData to detect when it actually changes (new session load)
	let previousInitialDataKey = '';

	// Reactive variables for dependency manager
	let allFields: FieldConfig[] = [];
	let processedSchema: any = null;

	// Reactive: Extract all fields from schema when it changes (used below for
	// scroll-to-first-error; reaction dependency tracking no longer lives here -
	// see the value-changes block, which reprocesses unconditionally).
	$: if (formSchema) {
		allFields = extractAllFields(formSchema);
	}

	$: capabilityModelFields = formSchema ? collectCapabilityModelFields(formSchema) : [];

	let compareSchemaKey = '';
	let publishedCompareSignature = '';

	function publishSchemaForCompare(schema: any, data: Record<string, any>, revision: number) {
		if (!tabId || !compareSchemaKey) return;
		const signature = compareSchemaSignature(compareSchemaKey, schema, data, revision);
		if (signature === publishedCompareSignature) return;
		publishedCompareSignature = signature;
		publishCompareSchema(tabId, compareSchemaKey, schemaForCompare(schema, data, (id) => sharedCapabilityCache.get(id)));
	}

	$: if (formSchema && formData) publishSchemaForCompare(formSchema, formData, capabilityRevision);
	$: if (formData) {
		for (const modelField of capabilityModelFields) {
			capabilityTracker.select(modelField, cloudModelId(formData[modelField]));
		}
	}

	// Fields with a server-validation error must render even if they're
	// `audience: 'advanced'` and we're in Simple mode (see `fieldErrors` prop).
	$: forceVisibleFieldNames = new Set(
		Object.keys(fieldErrors).filter((name) => fieldErrors[name]?.length)
	);

	// Reactive: Process schema with reactions when formData changes
	// This computes the processed schema and immediately applies value changes if needed
	$: if (formSchema && formData && forceVisibleFieldNames) {
		const settled = settleReactions(
			formData,
			formSchema,
			capabilityModelFields,
			capabilityRevision,
			initialLoadComplete
		);
		let data = settled.data;
		let pass = settled.pass;
		let unsettled = settled.unsettled;
		if (unsettled && !reactionNonConvergenceWarned) {
			const stillChanging = Object.keys(pass.valueChanges).filter((name) => !valuesEqual(data[name], pass.valueChanges[name]));
			if (stillChanging.length) {
				reactionNonConvergenceWarned = true;
				console.warn(`Form reactions did not settle after ${MAX_REACTION_ROUNDS} rounds; fields still changing: ${stillChanging.join(', ')}`);
			}
		}
		if (pass.hiddenByCapability.join('|') !== [...capabilityHiddenNames].join('|')) {
			capabilityHiddenNames = new Set(pass.hiddenByCapability);
		}
		if (tabId && compareSchemaKey && initialLoadComplete) {
			publishMissingModel(
				tabId,
				compareSchemaKey,
				missingRequiredModelField(extractAllFields(pass.processedSchema), data, unverifiedModelSlots)
			);
		}
		applyAudienceVisibilityToSchema(
			pass.processedSchema,
			audience ?? $formAudienceStore,
			forceVisibleFieldNames,
			videoDirectorActive
		);
		processedSchema = pass.processedSchema;
		if (data !== formData) {
			formData = data;
		}
	}

	let unverifiedModelSlots: ReadonlySet<string> = new Set();
	let reactionBaseline: Record<string, any> | null = null;
	let reactionPinned: Set<string> = new Set();

	function resetReactionBaseline(incoming: Record<string, any> | null | undefined) {
		reactionBaseline = null;
		reactionPinned = new Set(Object.keys(incoming ?? {}));
	}

	function settleReactions(
		start: Record<string, any>,
		schema: any,
		modelFields: string[],
		revision: number,
		loaded: boolean
	) {
		const settled = settleFormReactions(start, schema, {
			modelFields,
			capabilityOf: (modelId) => (revision >= 0 ? sharedCapabilityCache.get(modelId) : undefined),
			baseline: reactionBaseline,
			pinned: reactionPinned,
			loaded,
			maxRounds: MAX_REACTION_ROUNDS
		});
		if (loaded) reactionBaseline = settled.data;
		return settled;
	}

	// Load form schema
	async function loadFormSchema(force = false) {
		const requestId = schemaRequest.next();
		const requestPresetId = presetId;
		const requestMode = mode;
		const requestVariant = variant;
		cancelSchemaRetry();
		if (force) schemaRetryCount = 0;
		try {
			capabilityTracker.reset();
			publishedSnapshots = [];
			capabilityHiddenNames = new Set();
			schemaLoading = true;
			schemaError = '';
			initialLoadComplete = false;

			const schema = await loadPresetFormSchema(requestPresetId, requestMode, requestVariant, force);

			// A slower request for the previous preset must never replace the active form.
			if (!schemaRequest.isCurrent(requestId)) return;

			compareSchemaKey = `${requestPresetId}-${requestMode}-${requestVariant ?? ''}`;
			publishedCompareSignature = '';
			formSchema = schema;
			resetReactionBaseline(initialData);
			formData = hydrateFormData(schema, initialData);
			previousInitialDataKey = JSON.stringify(initialData);
			initialLoadComplete = true;
			schemaRetryCount = 0;
		} catch (error) {
			if (!schemaRequest.isCurrent(requestId)) return;
			logger.error('Failed to load form schema:', error);
			formSchema = null;
			schemaError = error instanceof Error ? error.message : 'Could not load the form schema.';
			scheduleSchemaRetry();
		} finally {
			if (schemaRequest.isCurrent(requestId)) schemaLoading = false;
		}
	}

	const modelSlotAutopick = createModelSlotAutopick({
		tabKey: tabId ?? '',
		search: searchAutopickSlot,
		check: checkSlotValue
	});

	function republishMissingModel() {
		if (tabId && compareSchemaKey && initialLoadComplete && processedSchema) {
			publishMissingModel(
				tabId,
				compareSchemaKey,
				missingRequiredModelField(extractAllFields(processedSchema), formData, unverifiedModelSlots)
			);
		}
	}

	function syncModelSlotAutopick(schema: any, loadKey: string) {
		if (!tabId) return;
		modelSlotAutopick.sync({
			loadKey,
			slots: collectAutopickSlots(Object.values(schema?.properties ?? {}) as FieldConfig[], formData, presetId, true),
			isPinned: (name) => reactionPinned.has(name),
			isEmpty: (name) => !hasModelValue({ type: 'model' }, formData[name]),
			currentWire: (name) => modelWire(formData[name]),
			onPending: (names) => {
				unverifiedModelSlots = names;
				republishMissingModel();
			},
			onPick: (slot, model) => {
				formData = { ...formData, [slot.name]: autopickValue(slot, model) };
			},
			onClear: (slot) => {
				const label = allFields.find((f) => f.name === slot.name)?.title || slot.name;
				if (reactionPinned.has(slot.name)) toasts.warning(unavailableNotice(label), 8000);
				formData = { ...formData, [slot.name]: clearedSlotValue(slot) };
			}
		});
	}

	$: if (initialLoadComplete && processedSchema && compareSchemaKey) {
		syncModelSlotAutopick(processedSchema, compareSchemaKey);
	}

	// Handle field changes
	function handleFieldChange(fieldName: string, value: any) {
		if (
			tabId &&
			isSlotClearEdit(
				allFields.some((f) => f.name === fieldName && f.type === 'model'),
				formData[fieldName],
				value
			)
		) {
			markSlotCleared(tabId, fieldName);
		}
		formData = {
			...formData,
			[fieldName]: value
		};
		onFieldEdit?.(fieldName);
	}

	// Provenance sibling, kept off the value channel (see MediaLoaderField's
	// onOriginChange) - still lands under the same `${fieldName}__origin` key
	// the backend has always read.
	function handleFieldOriginChange(fieldName: string, origin: unknown) {
		formData = {
			...formData,
			[`${fieldName}__origin`]: origin
		};
	}

	// Same treatment for the inpaint mask sibling - see MediaLoaderField's
	// onMaskChange. Still lands under `${fieldName}_inpaint_mask`; `undefined`
	// drops the mask (the field cleared it because its image changed).
	function handleFieldMaskChange(fieldName: string, maskPath: string | undefined) {
		formData = {
			...formData,
			[`${fieldName}_inpaint_mask`]: maskPath
		};
	}

	// Reactive: Load schema when preset, mode, or variant changes
	$: {
		const currentKey = `${presetId}-${mode}-${variant ?? ''}`;
		if (currentKey !== previousKey && presetId) {
			previousKey = currentKey;
			schemaRetryCount = 0;
			// The first normalized payload for a new schema must publish even if
			// it serializes the same as the previous schema's payload.
			lastPublishedFormDataKey = null;
			loadFormSchema();
			// A schema reload means a new preset/mode/variant is active - any
			// fieldErrors the parent is holding for the previous form no longer
			// apply (the field set itself may not even exist anymore).
			onSchemaKeyChange?.();
		}
	}

	// Reactive: scroll the first field with a server-validation error into view.
	// Keyed on the sorted set of erroring field names so this only fires once per
	// distinct error batch (e.g. a new failed submission), not on every unrelated
	// formData/store update.
	let lastScrolledErrorKey = '';
	$: {
		const erroredNames = Object.keys(fieldErrors).filter((name) => fieldErrors[name]?.length);
		const key = erroredNames.sort().join(',');
		if (key && key !== lastScrolledErrorKey) {
			lastScrolledErrorKey = key;
			scrollToFirstFieldError(erroredNames);
		} else if (!key) {
			lastScrolledErrorKey = '';
		}
	}

	function scrollToFirstFieldError(erroredNames: string[]) {
		if (typeof document === 'undefined' || erroredNames.length === 0) return;
		// Prefer schema document order (allFields) so "first" matches what the user
		// sees top-to-bottom; fall back to object key order if the schema hasn't
		// loaded the field yet.
		const firstInSchemaOrder = allFields.find((f) => f.name && erroredNames.includes(f.name));
		const targetName = firstInSchemaOrder?.name ?? erroredNames[0];
		// Wait a frame so the audience-reveal/tab-switch reactivity above has had a
		// chance to render the (possibly just-unhidden) target field.
		requestAnimationFrame(() => {
			const selector = `[data-field-name="${targetName.replace(/"/g, '\\"')}"]`;
			document.querySelector(selector)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
		});
	}

	// Reactive: Update formData when initialData changes (for session loading)
	// IMPORTANT: Only react to actual changes in initialData reference/content, NOT user edits to formData
	$: if (initialLoadComplete && initialData && formSchema) hydrateFromInitialData(initialData, formSchema, reloadGeneration);

	let appliedReloadGeneration = reloadGeneration;

	function hydrateFromInitialData(incoming: Record<string, any>, schema: any, generation: number) {
		if (generation !== appliedReloadGeneration) {
			appliedReloadGeneration = generation;
			publishedSnapshots = [];
		}
		// Create a stable key from initialData to detect when it actually changes
		const currentInitialDataKey = JSON.stringify(incoming);

		// Only update formData if initialData has changed (not just different from formData)
		// This prevents overwriting user edits while still allowing session loads to update the form
		if (currentInitialDataKey !== previousInitialDataKey && !isStalePublishEcho(currentInitialDataKey)) {
			previousInitialDataKey = currentInitialDataKey;
			// Server/session hydration can provide the same normalized payload as a
			// previous component state. It is still a new input generation and must
			// be forwarded once so the page can settle its one-shot session baseline.
			lastPublishedFormDataKey = null;

			const mergedData = hydrateFormData(schema, incoming);
			resetReactionBaseline(incoming);
			formData = mergedData;
		}
	}

	// Notify the parent immediately. The generate page unmounts inactive tabs,
	// and a debounce cancelled in onDestroy loses edits made just before a tab
	// switch. The tab store already debounces localStorage persistence, so this
	// does not turn field input into synchronous storage writes.
	let lastPublishedFormDataKey: string | null = null;
	let publishedSnapshots: string[] = [];

	function rememberPublish(key: string) {
		publishedSnapshots = [...publishedSnapshots.filter((stored) => stored !== key), key].slice(
			-PUBLISHED_SNAPSHOT_LIMIT
		);
	}

	function isStalePublishEcho(key: string): boolean {
		if (capabilityModelFields.length === 0) return false;
		if (key === publishedSnapshots[publishedSnapshots.length - 1]) return false;
		return publishedSnapshots.includes(key);
	}
	// An empty object is a complete normalized form too. It must publish once so
	// a hydrated session with no defaults can consume its pending saved baseline
	// before the user's first real field edit.
	$: if (initialLoadComplete && formData && onFormDataChange) {
		const flattenedData = flattenFormData(formData, capabilityHiddenNames);
		const flattenedDataKey = JSON.stringify(flattenedData);
		if (shouldPublishFormData(lastPublishedFormDataKey, flattenedData)) {
			lastPublishedFormDataKey = flattenedDataKey;
			previousInitialDataKey = flattenedDataKey;
			rememberPublish(flattenedDataKey);
			onFormDataChange(flattenedData);
		}
	}

	// Expose method to force schema reload (used by preset reload)
	export function forceReload(): void {
		clearFormulaDeclarations();
		previousKey = `${presetId}-${mode}-${variant ?? ''}`;
		lastPublishedFormDataKey = null;
		void loadFormSchema(true);
	}

	onDestroy(() => {
		if (tabId) clearMissingModel(tabId);
		capabilityTracker.destroy();
		modelSlotAutopick.dispose();
		cancelSchemaRetry();
		schemaRequest.invalidate();
	});
</script>

<section aria-label={formName}>
{#if schemaLoading}
	<div class="flex items-center justify-center py-8">
		<div class="spinner"></div>
		<span class="ml-3 text-fg-muted">Loading Form...</span>
	</div>
{:else if !formSchema}
	<div class="bg-danger/10 border border-danger/25 rounded-lg p-4">
		<p class="text-sm font-medium text-danger">Could not load form schema</p>
		{#if schemaError}<p class="mt-1 text-xs text-fg-muted">{schemaError}</p>{/if}
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/30 px-3 py-1.5 text-xs font-medium text-danger transition-colors hover:bg-danger/10"
			on:click={() => loadFormSchema(true)}
		>
			Retry
		</button>
	</div>
{:else}
	<div class="space-y-4">
		{#each Object.entries((processedSchema || formSchema).properties || {}) as [name, config]}
			<FormField
				{name}
				{config}
				bind:value={formData}
				onChange={handleFieldChange}
				onOriginChange={handleFieldOriginChange}
				onMaskChange={handleFieldMaskChange}
				fieldPath={name}
			/>
		{/each}
	</div>
{/if}
</section>
