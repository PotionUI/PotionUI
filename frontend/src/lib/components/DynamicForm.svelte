<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onDestroy, setContext } from 'svelte';
	import { writable } from 'svelte/store';
	import { api } from '$lib/services/api/index';
	import FormField from './form-fields/FormField.svelte';
	import {
		type FieldConfig,
		extractAllFields,
		processSchemaWithReactions
	} from '$lib/form/reactions';
	import { applyReactionValueChanges } from './dynamicFormReactionApply';
	import { getSchemaDefaults } from '$lib/form/defaults';
	import { createLatestRequestGuard, getCachedSchema } from '$lib/form/schemaCache';
	import { formAudienceStore } from '$lib/stores/formAudience';
	import { applyAudienceVisibilityToSchema } from '$lib/utils/audienceFilter';
	import { applyReadonlyToSchema } from '$lib/utils/readonlyFilter';
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
	// Whether the Video Director editor owns the active preset mode - hides
	// any field marked `hidden_when_video_director: true` (see
	// `$lib/utils/audienceFilter.ts`). Callers with no Director concept
	// simply don't pass it.
	export let videoDirectorActive = false;
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

	// State
	let formSchema: any = null;
	let formData: Record<string, any> = {};
	let schemaLoading = true;
	let schemaError = '';
	let initialLoadComplete = false;
	const schemaRequest = createLatestRequestGuard();

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

	// Fields with a server-validation error must render even if they're
	// `audience: 'advanced'` and we're in Simple mode (see `fieldErrors` prop).
	$: forceVisibleFieldNames = new Set(
		Object.keys(fieldErrors).filter((name) => fieldErrors[name]?.length)
	);

	// Reactive: Process schema with reactions when formData changes
	// This computes the processed schema and immediately applies value changes if needed
	$: if (formSchema && formData && forceVisibleFieldNames) {
		const result = processSchemaWithReactions(formSchema, formData);
		// Admin-locked (readonly) fields fold into `disabled` after reactions run,
		// so a reaction's set_disabled: false can never re-enable a field the admin
		// locked via a per-field form override.
		applyReadonlyToSchema(result.processedSchema);
		// Audience/Director filtering runs after reactions: a field already
		// hidden by a reaction stays hidden regardless of either; an
		// 'advanced' field is additionally hidden in 'simple' mode, and a
		// `hidden_when_video_director` field is additionally hidden while
		// `videoDirectorActive`. Only rendering changes - the field's
		// value/default stays in formData either way, so submission
		// (getFormData/flattenFormData) is unaffected.
		applyAudienceVisibilityToSchema(
			result.processedSchema,
			$formAudienceStore,
			forceVisibleFieldNames,
			videoDirectorActive
		);
		processedSchema = result.processedSchema;

		// Apply value changes immediately if needed (don't store as reactive variable).
		//
		// This reprocesses on every formData change unconditionally - there is
		// deliberately no "did a trigger field actually change" pre-check. The
		// loop guard is entirely inside applyReactionValueChanges: it reports
		// `changed: true` - and only then do we reassign `formData` - when a
		// value actually differs (structurally, not just by reference; see
		// valuesEqual there). Since processSchemaWithReactions is a pure function
		// of (formSchema, formData), a reassignment that already matches what a
		// reaction computes converges on the next reprocess with no further
		// reassignment, so this cannot re-trigger indefinitely.
		const valueChanges = result.valueChanges;
		if (valueChanges && Object.keys(valueChanges).length > 0 && initialLoadComplete) {
			const applied = applyReactionValueChanges(formData, valueChanges);
			if (applied.changed) {
				formData = applied.data;
			}
		}
	}

	// Helper to merge form data
	function mergeFormData(
		schemaDefaults: Record<string, any>,
		userData: Record<string, any> | null
	): Record<string, any> {
		if (!userData) return { ...schemaDefaults };

		const merged = { ...schemaDefaults };

		for (const key in userData) {
			if (key.endsWith('_tagFilters')) continue;

			// Check if this field has a corresponding model object in defaults
			if (merged[key] && typeof merged[key] === 'object' && merged[key].hasOwnProperty('modelPath')) {
				const modelPath = userData[key] || '';
				const tagFiltersKey = `${key}_tagFilters`;
				const tagFilters = userData[tagFiltersKey] || merged[key].tagFilters || [];

				merged[key] = {
					modelPath: modelPath,
					tagFilters: tagFilters
				};
			} else {
				merged[key] = userData[key];
			}
		}

		return merged;
	}

	// Helper to flatten form data for submission
	function flattenFormData(data: Record<string, any>): Record<string, any> {
		const flattened: Record<string, any> = {};

		for (const [key, value] of Object.entries(data)) {
			if (value && typeof value === 'object' && !Array.isArray(value) && value.constructor === Object) {
				// Check if this is a model selector object
				if (value.hasOwnProperty('modelPath') && value.hasOwnProperty('tagFilters')) {
					flattened[key] = value.modelPath;
					flattened[`${key}_tagFilters`] = value.tagFilters;
				}
				// Check if this is a MediaLoaderField object (has path property)
				else if (value.hasOwnProperty('path')) {
					flattened[key] = value.path;
				} else {
					flattened[key] = value;
				}
			} else {
				flattened[key] = value;
			}
		}

		return flattened;
	}

	// Load form schema
	async function loadFormSchema(force = false) {
		const requestId = schemaRequest.next();
		const requestPresetId = presetId;
		const requestMode = mode;
		const requestVariant = variant;
		try {
			schemaLoading = true;
			schemaError = '';
			initialLoadComplete = false;

			const schema = await getCachedSchema(
				requestPresetId,
				requestMode,
				async () => {
					const response = await api.getPresetFormSchema(requestPresetId, requestMode, requestVariant);
					if (!response.success || !response.data?.form_schema) {
						throw new Error(response.error || 'The preset did not return a form schema.');
					}
					return response.data.form_schema;
				},
				force,
				requestVariant
			);

			// A slower request for the previous preset must never replace the active form.
			if (!schemaRequest.isCurrent(requestId)) return;

			formSchema = schema;
			const schemaDefaults = getSchemaDefaults(schema);
			formData = mergeFormData(schemaDefaults, initialData);
			previousInitialDataKey = JSON.stringify(initialData);
			initialLoadComplete = true;
		} catch (error) {
			if (!schemaRequest.isCurrent(requestId)) return;
			logger.error('Failed to load form schema:', error);
			formSchema = null;
			schemaError = error instanceof Error ? error.message : 'Could not load the form schema.';
		} finally {
			if (schemaRequest.isCurrent(requestId)) schemaLoading = false;
		}
	}

	// Handle field changes
	function handleFieldChange(fieldName: string, value: any) {
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
	$: if (initialLoadComplete && initialData && formSchema) {
		// Create a stable key from initialData to detect when it actually changes
		const currentInitialDataKey = JSON.stringify(initialData);

		// Only update formData if initialData has changed (not just different from formData)
		// This prevents overwriting user edits while still allowing session loads to update the form
		if (currentInitialDataKey !== previousInitialDataKey) {
			previousInitialDataKey = currentInitialDataKey;
			// Server/session hydration can provide the same normalized payload as a
			// previous component state. It is still a new input generation and must
			// be forwarded once so the page can settle its one-shot session baseline.
			lastPublishedFormDataKey = null;

			// Get root schema for defaults
			const schemaDefaults = getSchemaDefaults(formSchema);

			// Merge initialData with schema defaults
			const mergedData = mergeFormData(schemaDefaults, initialData);
			formData = mergedData;
		}
	}

	// Notify the parent immediately. The generate page unmounts inactive tabs,
	// and a debounce cancelled in onDestroy loses edits made just before a tab
	// switch. The tab store already debounces localStorage persistence, so this
	// does not turn field input into synchronous storage writes.
	let lastPublishedFormDataKey: string | null = null;
	// An empty object is a complete normalized form too. It must publish once so
	// a hydrated session with no defaults can consume its pending saved baseline
	// before the user's first real field edit.
	$: if (initialLoadComplete && formData && onFormDataChange) {
		const flattenedData = flattenFormData(formData);
		const flattenedDataKey = JSON.stringify(flattenedData);
		if (shouldPublishFormData(lastPublishedFormDataKey, flattenedData)) {
			lastPublishedFormDataKey = flattenedDataKey;
			previousInitialDataKey = flattenedDataKey;
			onFormDataChange(flattenedData);
		}
	}

	// Expose method to get current form data
	export function getFormData(): Record<string, any> {
		return flattenFormData(formData);
	}

	// Expose method to force schema reload (used by preset reload)
	export function forceReload(): void {
		previousKey = `${presetId}-${mode}-${variant ?? ''}`;
		lastPublishedFormDataKey = null;
		void loadFormSchema(true);
	}

	onDestroy(() => {
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
