<script lang="ts">
	import { untrack } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { invalidatePresets } from '$lib/stores/presetsCatalog';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';
	import { Alert, Button, Input } from '$lib/components/ui';
	import { DetailSection, DetailField } from '$lib/components/detail';
	import PresetThumbnail from '$lib/components/preset/PresetThumbnail.svelte';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import CheckboxGroupField from '$lib/components/form-fields/CheckboxGroupField.svelte';
	import {
		DISPLAY_DESCRIPTION_MAX,
		buildDisplayPayload,
		buildModesPayload,
		discardDisplayEditor,
		displayDirtyKeys,
		displayEditorDirtyCount,
		displayEditorFailed,
		displayEditorFor,
		displayEditorSaved,
		modesDirtyCount,
		validateDisplayDraft,
		validateModesDraft,
		withOfferedToggled,
		type DisplayEditor,
		type DisplayServerField
	} from './presetDisplayDraft';
	import { cleanFieldErrors, presetApiFailure, presetResponseFailure } from './presetApiFailure';
	import { COVER_ACCEPT, mediaPickItem, resolveCoverFileFromMediaItem } from './presetCoverPick';
	import type { APIResponse, PresetAdminDetail } from '$lib/types/api';

	let {
		presetId,
		initialDetail,
		category = null,
		dirtyCount = $bindable(0),
		saving = $bindable(false),
		onChanged
	}: {
		presetId: string;
		initialDetail: PresetAdminDetail;
		category?: string | null;
		dirtyCount?: number;
		saving?: boolean;
		onChanged?: (detail: PresetAdminDetail) => void;
	} = $props();

	let editor = $state<DisplayEditor>(displayEditorFor(untrack(() => initialDetail)));
	let coverPickerOpen = $state(false);
	let coverPick = $state<unknown>(null);
	let coverBusy = $state(false);
	let coverError = $state('');

	const detail = $derived(editor.detail);
	const draft = $derived(editor.draft);
	const modesDraft = $derived(editor.modes);
	const attempted = $derived(editor.attempted);
	const serverErrors = $derived(editor.serverErrors);
	const generalErrors = $derived(editor.generalErrors);
	const isMirror = $derived(detail.is_mirror);
	const dirtyKeys = $derived(displayDirtyKeys(draft, detail));
	const modesDirty = $derived(isMirror ? modesDirtyCount(modesDraft, detail.modes) : 0);
	const clientErrors = $derived(validateDisplayDraft(draft, isMirror));
	const modesError = $derived(isMirror ? validateModesDraft(modesDraft) : null);
	const fileName = $derived(detail.file?.name ?? detail.source_name ?? '');
	const fileDescription = $derived(detail.file?.description ?? '');
	const coverPath = $derived(detail.display.cover ?? detail.file?.cover ?? null);
	const coverOwner = $derived(detail.display.cover ? detail.preset_id : (detail.source_preset_id ?? detail.preset_id));
	const modeOptions = $derived(
		detail.modes.available.map((mode) => ({ value: mode.name, label: modeLabel(mode.name, mode.label) }))
	);
	const offeredOptions = $derived(modeOptions.filter((option) => modesDraft.offered.includes(option.value)));

	const nameError = $derived(
		serverErrors.name ?? (attempted || dirtyKeys.includes('name') ? clientErrors.name : undefined)
	);
	const descriptionError = $derived(serverErrors.description ?? clientErrors.description);
	const shownModesError = $derived(serverErrors.modes ?? (attempted || modesDirty > 0 ? modesError : null));

	$effect(() => {
		dirtyCount = displayEditorDirtyCount(editor);
	});

	function clearServerError(field: DisplayServerField) {
		if (editor.serverErrors[field]) editor = { ...editor, serverErrors: { ...editor.serverErrors, [field]: undefined } };
	}

	function setName(value: string) {
		editor = { ...editor, draft: { ...editor.draft, name: value } };
		clearServerError('name');
	}

	function setDescription(value: string) {
		editor = { ...editor, draft: { ...editor.draft, description: value } };
		clearServerError('description');
	}

	function setOffered(_name: string, value: unknown) {
		const next = Array.isArray(value) ? value.map(String) : [];
		editor = { ...editor, modes: withOfferedToggled(editor.modes, next, editor.detail.modes.available) };
		clearServerError('modes');
	}

	function setFirst(value: string) {
		editor = { ...editor, modes: { ...editor.modes, first: value } };
		clearServerError('modes');
	}

	function unwrap(response: APIResponse<PresetAdminDetail>, fallback: string): PresetAdminDetail {
		if (!response?.success || !response.data) {
			const failure = presetResponseFailure(response, fallback);
			throw Object.assign(new Error(failure.message), { presetFailure: failure });
		}
		return response.data;
	}

	function failureOf(error: unknown, fallback: string) {
		return (
			(error as { presetFailure?: ReturnType<typeof presetApiFailure> } | null)?.presetFailure ??
			presetApiFailure(error, fallback)
		);
	}

	export async function save() {
		editor = { ...editor, attempted: true };
		if (Object.keys(clientErrors).length > 0 || (modesDirty > 0 && modesError)) return;
		if (dirtyKeys.length === 0 && modesDirty === 0) return;
		saving = true;
		editor = { ...editor, serverErrors: {}, generalErrors: [] };
		try {
			let next = editor.detail;
			if (dirtyKeys.length > 0) {
				next = unwrap(
					await api.updatePresetDisplay(presetId, buildDisplayPayload(editor.draft, editor.detail.display.cover)),
					'Could not save the display settings'
				);
				editor = { ...editor, detail: next };
			}
			if (modesDirty > 0) {
				next = unwrap(
					await api.updatePresetMirrorModes(presetId, buildModesPayload(editor.modes, editor.detail.modes.available)),
					'Could not save the modes'
				);
			}
			editor = displayEditorSaved(editor, next);
			onChanged?.(next);
			invalidatePresets();
			toasts.success('Display saved');
		} catch (error) {
			logger.error('Failed to save preset display:', error);
			const result = displayEditorFailed(editor, failureOf(error, 'Could not save the display settings'));
			editor = result.editor;
			if (result.toast) toasts.error(result.toast);
		} finally {
			saving = false;
		}
	}

	export function discard() {
		editor = discardDisplayEditor(editor);
	}

	function toggleCoverPicker() {
		coverError = '';
		coverPickerOpen = !coverPickerOpen;
	}

	async function handleCoverPick(_name: string, value: unknown) {
		coverPick = value;
		const item = mediaPickItem(value);
		if (!item) return;
		coverError = '';
		coverBusy = true;
		try {
			const resolved = await resolveCoverFileFromMediaItem(item);
			if (resolved.error || !resolved.file) {
				coverError = resolved.error || 'Could not load the selected file.';
				coverPick = null;
				return;
			}
			const next = unwrap(await api.uploadPresetDisplayCover(presetId, resolved.file), 'Could not upload the cover');
			editor = { ...editor, detail: next };
			onChanged?.(next);
			invalidatePresets();
			coverPick = null;
			coverPickerOpen = false;
			toasts.success('Cover updated');
		} catch (error) {
			logger.error('Failed to upload preset cover:', error);
			const failure = failureOf(error, 'Could not upload the cover');
			coverError = cleanFieldErrors(failure.errors) || failure.message;
		} finally {
			coverBusy = false;
		}
	}

	async function resetCover() {
		coverError = '';
		coverBusy = true;
		try {
			const next = unwrap(
				await api.updatePresetDisplay(presetId, {
					name: detail.display.name,
					description: detail.display.description,
					cover: null
				}),
				'Could not reset the cover'
			);
			editor = { ...editor, detail: next };
			onChanged?.(next);
			invalidatePresets();
			toasts.success('Cover reset');
		} catch (error) {
			logger.error('Failed to reset preset cover:', error);
			const failure = failureOf(error, 'Could not reset the cover');
			coverError = cleanFieldErrors(failure.errors) || failure.message;
		} finally {
			coverBusy = false;
		}
	}
</script>

<div class="space-y-4" data-preset-display-tab>
	{#if generalErrors.length}
		<Alert variant="danger" density="compact" icon>
			{generalErrors.join(' ')}
		</Alert>
	{/if}

	<DetailSection label="Name and description">
		<div class="space-y-4">
			<DetailField
				label={isMirror ? 'Name' : 'Display name'}
				id="preset-display-name"
				error={nameError}
				help={isMirror ? 'What users see in the preset picker.' : fileName ? `Leave empty to use “${fileName}”.` : 'Leave empty to use the preset’s own name.'}
			>
				<Input
					id="preset-display-name"
					type="text"
					class="w-full"
					value={draft.name}
					placeholder={fileName}
					invalid={!!nameError}
					oninput={(event: Event) => setName((event.target as HTMLInputElement).value)}
				/>
			</DetailField>
			<DetailField
				label="Description"
				id="preset-display-description"
				wide
				error={descriptionError}
				help={isMirror ? 'Leave empty to use the source preset’s description.' : 'Leave empty to use the preset’s own description.'}
			>
				<textarea
					id="preset-display-description"
					class="input w-full {descriptionError ? 'border-danger focus:ring-danger' : ''}"
					rows="4"
					value={draft.description}
					placeholder={fileDescription}
					aria-invalid={descriptionError ? true : undefined}
					oninput={(event) => setDescription((event.target as HTMLTextAreaElement).value)}
				></textarea>
				<p class="mt-1 text-right font-mono text-xs tabular-nums text-fg-subtle">{draft.description.length} / {DISPLAY_DESCRIPTION_MAX}</p>
			</DetailField>
		</div>
	</DetailSection>

	<DetailSection label="Cover">
		{#snippet headerExtra()}
			{#if detail.display.cover}
				<Button variant="ghost" size="xs" icon="refresh" disabled={coverBusy} onclick={resetCover}>Use preset’s cover</Button>
			{/if}
			<Button variant="secondary" size="xs" icon="upload" disabled={coverBusy} onclick={toggleCoverPicker}>
				{coverPickerOpen ? 'Close' : 'Change cover'}
			</Button>
		{/snippet}
		<div class="flex flex-col gap-4 sm:flex-row sm:items-start">
			<PresetThumbnail
				presetId={coverOwner}
				presetName={detail.effective.name ?? fileName}
				cover={coverPath}
				{category}
				size="w-28 h-28 sm:w-32 sm:h-32"
				variant="medium"
			/>
			<div class="min-w-0 flex-1 space-y-2">
				<p class="text-sm text-fg">{detail.display.cover ? 'Custom cover' : 'The preset’s own cover'}</p>
				<p class="text-sm text-fg-muted">PNG, JPG or WEBP up to 10 MB, or a short MP4 or WEBM up to 1.5 MB. A new cover is saved right away.</p>
				{#if coverError}
					<p class="text-sm text-danger" role="alert">{coverError}</p>
				{/if}
				{#if coverPickerOpen}
					<div class="max-w-md">
						<MediaLoaderField
							name="preset_cover_pick"
							value={coverPick}
							onChange={handleCoverPick}
							config={{ accept: COVER_ACCEPT }}
							compact
							compactFullWidth
						/>
					</div>
				{/if}
			</div>
		</div>
	</DetailSection>

	{#if isMirror}
		<DetailSection label="Modes">
			{#if detail.modes.available.length === 0}
				<p class="text-sm text-fg-muted">The source preset is gone, so there are no modes to pick from.</p>
			{:else}
				<div class="space-y-4" data-mirror-modes>
					<CheckboxGroupField
						name="mirror_offered_modes"
						config={{ title: 'Modes users can pick', options: modeOptions, layout: 'horizontal' }}
						value={modesDraft.offered}
						onChange={setOffered}
					/>
					<DetailField
						label="Opens first"
						id="mirror-first-mode"
						error={shownModesError}
						help="The mode a new tab starts in."
					>
						<select
							id="mirror-first-mode"
							class="input w-56"
							value={modesDraft.first}
							disabled={offeredOptions.length === 0}
							onchange={(event) => setFirst((event.target as HTMLSelectElement).value)}
						>
							{#each offeredOptions as option (option.value)}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
					</DetailField>
				</div>
			{/if}
		</DetailSection>
	{/if}
</div>
