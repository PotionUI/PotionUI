<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { Alert, Input } from '$lib/components/ui';
	import { DetailField } from '$lib/components/detail';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import CheckboxGroupField from '$lib/components/form-fields/CheckboxGroupField.svelte';
	import {
		DISPLAY_DESCRIPTION_MAX,
		buildModesPayload,
		normalizedDescription,
		normalizedName,
		validateDisplayDraft,
		validateModesDraft,
		withOfferedToggled,
		type ModesDraft
	} from './presetDisplayDraft';
	import { assignFieldErrors, presetApiFailure, presetResponseFailure } from './presetApiFailure';
	import { COVER_ACCEPT, mediaPickItem, resolveCoverFileFromMediaItem } from './presetCoverPick';
	import type { PresetAdminDetail, PresetAdminModeOption } from '$lib/types/api';

	type Field = 'name' | 'description' | 'cover' | 'modes';

	let { onCreated }: { onCreated: (detail: PresetAdminDetail) => void } = $props();

	let isOpen = $state(false);
	let saving = $state(false);
	let sourceId = $state('');
	let sourceName = $state('');
	let sourceDescription = $state('');
	let available = $state<PresetAdminModeOption[]>([]);
	let name = $state('');
	let description = $state('');
	let modesDraft = $state<ModesDraft>({ offered: [], first: '' });
	let coverPick = $state<unknown>(null);
	let coverFile = $state<File | null>(null);
	let coverBusy = $state(false);
	let attempted = $state(false);
	let serverErrors = $state<Partial<Record<Field, string>>>({});
	let generalError = $state('');

	const modeOptions = $derived(available.map((mode) => ({ value: mode.name, label: modeLabel(mode.name, mode.label) })));
	const offeredOptions = $derived(modeOptions.filter((option) => modesDraft.offered.includes(option.value)));
	const clientErrors = $derived(validateDisplayDraft({ name, description }, true));
	const modesError = $derived(available.length ? validateModesDraft(modesDraft) : null);
	const nameError = $derived(serverErrors.name ?? (attempted ? clientErrors.name : undefined));
	const descriptionError = $derived(serverErrors.description ?? clientErrors.description);
	const shownModesError = $derived(serverErrors.modes ?? modesError ?? undefined);
	const canCreate = $derived(!!normalizedName(name) && !coverBusy && !modesError && !clientErrors.description);

	export function open(source: { id: string; name: string; description?: string | null }, modes: PresetAdminModeOption[]) {
		sourceId = source.id;
		sourceName = source.name;
		sourceDescription = source.description ?? '';
		available = modes;
		name = '';
		description = '';
		modesDraft = { offered: modes.map((mode) => mode.name), first: modes[0]?.name ?? '' };
		coverPick = null;
		coverFile = null;
		coverBusy = false;
		attempted = false;
		serverErrors = {};
		generalError = '';
		isOpen = true;
	}

	function close() {
		if (!saving) isOpen = false;
	}

	function setName(value: string) {
		name = value;
		if (serverErrors.name) serverErrors = { ...serverErrors, name: undefined };
	}

	function setDescription(value: string) {
		description = value;
		if (serverErrors.description) serverErrors = { ...serverErrors, description: undefined };
	}

	function setOffered(_field: string, value: unknown) {
		modesDraft = withOfferedToggled(modesDraft, Array.isArray(value) ? value.map(String) : [], available);
		if (serverErrors.modes) serverErrors = { ...serverErrors, modes: undefined };
	}

	async function handleCoverPick(_field: string, value: unknown) {
		coverPick = value;
		const item = mediaPickItem(value);
		if (!item) {
			coverFile = null;
			return;
		}
		coverBusy = true;
		serverErrors = { ...serverErrors, cover: undefined };
		try {
			const resolved = await resolveCoverFileFromMediaItem(item);
			if (resolved.error || !resolved.file) {
				serverErrors = { ...serverErrors, cover: resolved.error ?? 'Could not load the selected file.' };
				coverPick = null;
				coverFile = null;
				return;
			}
			coverFile = resolved.file;
		} finally {
			coverBusy = false;
		}
	}

	async function create(): Promise<boolean> {
		attempted = true;
		if (!canCreate) return false;
		saving = true;
		serverErrors = {};
		generalError = '';
		try {
			const modes = available.length ? buildModesPayload(modesDraft, available) : { offered: null, first: null };
			const response = await api.createPresetMirror(sourceId, {
				name: normalizedName(name) ?? '',
				description: normalizedDescription(description),
				cover: null,
				offered_modes: modes.offered,
				first_mode: modes.first
			});
			if (!response.success || !response.data) {
				const failure = presetResponseFailure(response, 'Could not create the mirror');
				throw Object.assign(new Error(failure.message), { presetFailure: failure });
			}
			let created = response.data;
			if (coverFile) {
				try {
					const coverResponse = await api.uploadPresetDisplayCover(created.preset_id, coverFile);
					if (coverResponse.success && coverResponse.data) created = coverResponse.data;
					else toasts.warning('Mirror created, but its cover could not be saved. Add it on the Display tab.');
				} catch (error) {
					logger.error('Failed to upload mirror cover:', error);
					toasts.warning('Mirror created, but its cover could not be saved. Add it on the Display tab.');
				}
			}
			isOpen = false;
			toasts.success(`${created.effective.name ?? normalizedName(name)} created`);
			onCreated(created);
			return true;
		} catch (error) {
			logger.error('Failed to create mirror:', error);
			const failure =
				(error as { presetFailure?: ReturnType<typeof presetApiFailure> } | null)?.presetFailure ??
				presetApiFailure(error, 'Could not create the mirror');
			if (failure.code === 'invalid_modes') {
				serverErrors = { modes: failure.errors.join(' ') || failure.message };
			} else {
				const assigned = assignFieldErrors(failure.errors, ['name', 'description', 'cover'] as const);
				serverErrors = assigned.byField;
				generalError = [...assigned.general, ...(failure.errors.length ? [] : [failure.message])].join(' ');
			}
			return false;
		} finally {
			saving = false;
		}
	}
</script>

<ConfirmDialog
	{isOpen}
	title="Create mirror"
	subtitle={sourceName ? `Based on ${sourceName}` : ''}
	size="lg"
	busy={saving}
	confirmLabel="Create mirror"
	confirmDisabled={!canCreate}
	onConfirm={create}
	onCancel={close}
>
	<div class="space-y-4 p-6" data-create-mirror-form>
		<p class="text-sm text-fg-muted">
			A mirror is its own preset that uses {sourceName || 'this preset'}’s files. It starts with the same form
			settings and is visible to nobody until you give people access.
		</p>

		{#if generalError}
			<Alert variant="danger" density="compact" icon>{generalError}</Alert>
		{/if}

		<DetailField label="Name" id="create-mirror-name" wide error={nameError} help="What users see in the preset picker.">
			<Input
				id="create-mirror-name"
				type="text"
				class="w-full"
				value={name}
				placeholder="e.g. Film Covers"
				invalid={!!nameError}
				oninput={(event: Event) => setName((event.target as HTMLInputElement).value)}
			/>
		</DetailField>

		<DetailField
			label="Description"
			id="create-mirror-description"
			wide
			error={descriptionError}
			help="Optional. Leave empty to use the source preset’s description."
		>
			<textarea
				id="create-mirror-description"
				class="input w-full {descriptionError ? 'border-danger focus:ring-danger' : ''}"
				rows="3"
				value={description}
				placeholder={sourceDescription}
				maxlength={DISPLAY_DESCRIPTION_MAX}
				oninput={(event) => setDescription((event.target as HTMLTextAreaElement).value)}
			></textarea>
		</DetailField>

		<div class="min-w-0">
			<span class="mb-1 block text-sm font-medium text-fg-muted">Cover</span>
			<MediaLoaderField
				name="create_mirror_cover"
				value={coverPick}
				onChange={handleCoverPick}
				config={{ accept: COVER_ACCEPT }}
				compact
				compactFullWidth
			/>
			<p class="mt-1 text-sm {serverErrors.cover ? 'text-danger' : 'text-fg-subtle'}">
				{serverErrors.cover ?? 'Optional. Leave empty to use the source preset’s cover.'}
			</p>
		</div>

		{#if available.length}
			<CheckboxGroupField
				name="create_mirror_modes"
				config={{ title: 'Modes users can pick', options: modeOptions, layout: 'horizontal' }}
				value={modesDraft.offered}
				onChange={setOffered}
			/>
			<DetailField label="Opens first" id="create-mirror-first-mode" error={shownModesError} help="The mode a new tab starts in.">
				<select
					id="create-mirror-first-mode"
					class="input w-56"
					value={modesDraft.first}
					disabled={offeredOptions.length === 0}
					onchange={(event) => (modesDraft = { ...modesDraft, first: (event.target as HTMLSelectElement).value })}
				>
					{#each offeredOptions as option (option.value)}
						<option value={option.value}>{option.label}</option>
					{/each}
				</select>
			</DetailField>
		{/if}
	</div>
</ConfirmDialog>
