<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import MediaPreviewModal from '$lib/components/modals/MediaPreviewModal.svelte';
	import UploadLibraryModal from '$lib/components/modals/UploadLibraryModal.svelte';
	import type { PickedMedia } from '$lib/components/modals/mediaPickerSelection';
	import { fileDrop, readPastedImage } from '$lib/components/form-fields/mediaFieldInput';
	import { Button, CopyButton, EmptyState, IconButton, LoadErrorState, Menu, Spinner } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import { api } from '$lib/services/api/index';
	import { confirmDialog } from '$lib/stores/confirm';
	import { bumpKits, forgetKit, rememberKit } from '$lib/stores/kits';
	import { toasts } from '$lib/stores/toast';
	import { primaryModifier } from '$lib/utils/platform';
	import { moveItem } from '$lib/components/form-fields/mediaLoaderReorder';
	import { isNameClash, kitErrorMessage, kitErrorSuggestion } from '$lib/kits/kitErrors';
	import { kindOfMediaType, describeLimit, picksFromLibraryItems, picksNames, takenNames, toNewItems, type KitPick } from '$lib/kits/kitPicks';
	import { formatItemCount } from '$lib/kits/kitCounts';
	import { formatKitPath } from '$lib/kits/kitPath';
	import { defaultItemName } from '$lib/kits/kitNaming';
	import { KIT_ITEM_LIMIT, type KitDetail, type KitItem, type KitLabelCount, type KitPatch, type NewKitItem } from '$lib/kits/types';
	import KitDetailsDialog from './KitDetailsDialog.svelte';
	import KitItemTile from './KitItemTile.svelte';
	import KitKindCounts from './KitKindCounts.svelte';
	import KitPath from './KitPath.svelte';
	import KitTextDialog from './KitTextDialog.svelte';

	let {
		kitId,
		onBack,
		onDeleted,
		onOpenKit
	}: {
		kitId: string;
		onBack: () => void;
		onDeleted: () => void;
		onOpenKit: (id: string) => void;
	} = $props();

	let kit = $state<KitDetail | null>(null);
	let loading = $state(true);
	let failed = $state(false);
	let labels = $state<KitLabelCount[]>([]);
	let titleDraft = $state('');
	let showDetails = $state(false);
	let textDialog = $state<{ item: KitItem | null } | null>(null);
	let libraryOpen = $state(false);
	let historyOpen = $state(false);
	let preview = $state<KitItem | null>(null);
	let dragId = $state<string | null>(null);
	let dropIndex = $state<number | null>(null);
	let dropping = $state(false);
	let working = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	const modifier = primaryModifier();

	let items = $derived(kit?.items ?? []);
	let taken = $derived(takenNames(items));
	let full = $derived(items.length >= KIT_ITEM_LIMIT);

	const menuItems: MenuItem[] = [
		{ id: 'library', label: 'From library', icon: 'grid' },
		{ id: 'history', label: 'From history', icon: 'clock' },
		{ id: 'upload', label: 'Upload files', icon: 'upload' },
		{ id: 'paste', label: `Paste from clipboard`, icon: 'clipboard-list' },
		{ id: 'sep', label: '', separator: true },
		{ id: 'text', label: 'Write text', icon: 'document' }
	];

	async function load() {
		loading = true;
		failed = false;
		try {
			const response = await api.getKit(kitId);
			if (response.success && response.data) {
				kit = response.data;
				titleDraft = response.data.name;
				rememberKit(response.data);
			} else failed = true;
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
		void api
			.listKits({ page_size: 1 })
			.then((response) => {
				if (response.success && response.data) labels = response.data.labels;
			})
			.catch(() => undefined);
	}

	function adopt(next: KitDetail) {
		kit = next;
		titleDraft = next.name;
		rememberKit(next);
		bumpKits();
	}

	function problem(source: unknown, fallback: string) {
		toasts.error(kitErrorMessage(source, fallback));
	}

	async function commitTitle() {
		if (!kit) return;
		const next = titleDraft.trim();
		if (!next || next === kit.name) {
			titleDraft = kit.name;
			return;
		}
		const result = await saveDetails({ name: next });
		if (result.error) {
			titleDraft = kit.name;
			toasts.error(result.error);
		}
	}

	async function saveDetails(patch: KitPatch): Promise<{ error: string | null; clash?: boolean }> {
		if (!kit || Object.keys(patch).length === 0) return { error: null };
		try {
			const response = await api.updateKit(kit.id, patch);
			if (!response.success || !response.data) {
				return { error: kitErrorMessage(response, 'Could not save the kit.'), clash: isNameClash(response) };
			}
			adopt({ ...kit, ...response.data, items: response.data.items ?? kit.items });
			return { error: null };
		} catch (caught) {
			return { error: kitErrorMessage(caught, 'Could not save the kit.'), clash: isNameClash(caught) };
		}
	}

	async function addItems(picks: KitPick[]) {
		if (!kit || picks.length === 0) return;
		const limit = describeLimit(items.length, picks.length, KIT_ITEM_LIMIT);
		if (limit) {
			toasts.error(limit);
			return;
		}
		working = true;
		try {
			const response = await api.addKitItems(kit.id, toNewItems(picks, picksNames(picks, taken)));
			if (response.success && response.data) {
				adopt(response.data);
				toasts.success(`Added ${picks.length} ${picks.length === 1 ? 'item' : 'items'}`);
			} else problem(response, 'Could not add the items.');
		} catch (caught) {
			problem(caught, 'Could not add the items.');
		} finally {
			working = false;
		}
	}

	function handleLibraryConfirm(_keys: string[], picked: PickedMedia[]) {
		const libraryItems = picked.map((entry) => entry.item).filter((entry): entry is NonNullable<typeof entry> => !!entry);
		void addItems(picksFromLibraryItems(libraryItems));
	}

	async function handleHistoryConfirm(picked: PickedMedia[]) {
		const picks: KitPick[] = [];
		for (const entry of picked) {
			if (!entry.file) continue;
			try {
				const response = await api.copyGenerationFileToLibrary(String(entry.file.id));
				if (response.success && response.data?.item) picks.push(...picksFromLibraryItems([response.data.item]));
			} catch (caught) {
				problem(caught, 'Could not copy a file to the Library.');
			}
		}
		await addItems(picks);
	}

	async function uploadFiles(files: File[]) {
		if (!kit) return;
		working = true;
		let added = 0;
		for (const file of files) {
			if (!kindOfMediaType(file.type.split('/')[0])) {
				toasts.error(`${file.name} is not a picture, clip or sound.`);
				continue;
			}
			const limit = describeLimit(items.length + added, 1, KIT_ITEM_LIMIT);
			if (limit) {
				toasts.error(limit);
				break;
			}
			try {
				const response = await api.uploadKitItem(kit.id, file);
				if (response.success && response.data) {
					kit = response.data;
					added += 1;
				} else toasts.error(`${file.name} could not be uploaded.`);
			} catch (caught) {
				problem(caught, `${file.name} could not be uploaded.`);
			}
		}
		working = false;
		if (added > 0 && kit) {
			adopt(kit);
			toasts.success(`Added ${added} ${added === 1 ? 'item' : 'items'}`);
		}
	}

	function handleFileChosen(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const files = Array.from(input.files ?? []);
		input.value = '';
		if (files.length > 0) void uploadFiles(files);
	}

	function handlePaste(event: ClipboardEvent) {
		const target = event.target as HTMLElement | null;
		if (target && ['INPUT', 'TEXTAREA'].includes(target.tagName)) return;
		if (document.querySelector('[aria-modal="true"]')) return;
		const outcome = readPastedImage(event.clipboardData?.items);
		if ('file' in outcome) {
			event.preventDefault();
			void uploadFiles([outcome.file]);
		}
	}

	async function pasteFromClipboard() {
		try {
			const entries = await navigator.clipboard.read();
			for (const entry of entries) {
				const type = entry.types.find((candidate) => candidate.startsWith('image/'));
				if (!type) continue;
				const blob = await entry.getType(type);
				await uploadFiles([new File([blob], `pasted-${Date.now()}.png`, { type })]);
				return;
			}
			toasts.info('Nothing pasteable in the clipboard. Copy an image first.');
		} catch {
			toasts.info(`Press ${modifier} V to paste an image.`);
		}
	}

	function pickSource(id: string) {
		if (full && id !== 'text') {
			toasts.error(describeLimit(items.length, 1, KIT_ITEM_LIMIT) ?? 'This kit is full.');
			return;
		}
		if (id === 'library') libraryOpen = true;
		else if (id === 'history') historyOpen = true;
		else if (id === 'upload') fileInput?.click();
		else if (id === 'paste') void pasteFromClipboard();
		else if (id === 'text') textDialog = { item: null };
	}

	async function saveText(text: string): Promise<string | null> {
		if (!kit) return 'Could not save.';
		const editing = textDialog?.item ?? null;
		try {
			if (editing) {
				const response = await api.updateKitItem(kit.id, editing.id, { text });
				if (!response.success || !response.data) return kitErrorMessage(response, 'Could not save the text.');
				adopt(response.data);
				return null;
			}
			const limit = describeLimit(items.length, 1, KIT_ITEM_LIMIT);
			if (limit) return limit;
			const body: NewKitItem = { kind: 'text', name: defaultItemName('text', taken), text };
			const response = await api.addKitItems(kit.id, [body]);
			if (!response.success || !response.data) return kitErrorMessage(response, 'Could not add the text.');
			adopt(response.data);
			return null;
		} catch (caught) {
			return kitErrorMessage(caught, 'Could not save the text.');
		}
	}

	async function renameItem(item: KitItem, name: string): Promise<{ ok: boolean; clash?: boolean; suggestion?: string | null }> {
		if (!kit) return { ok: false };
		try {
			const response = await api.updateKitItem(kit.id, item.id, { name });
			if (!response.success || !response.data) return { ok: false, clash: isNameClash(response), suggestion: kitErrorSuggestion(response) };
			kit = response.data;
			rememberKit(response.data);
			return { ok: true };
		} catch (caught) {
			return { ok: false, clash: isNameClash(caught), suggestion: kitErrorSuggestion(caught) };
		}
	}

	async function removeItem(item: KitItem) {
		if (!kit) return;
		const confirmed = await confirmDialog({
			title: 'Remove item?',
			message: `Remove ${item.name} from ${kit.name}? Places that cite ${formatKitPath(kit.handle, item.name)} keep their chip, drawn as removed, and leave it out. The file stays in the Library${item.kind === 'text' ? '; the written text is deleted' : ''}.`,
			variant: 'danger',
			confirmLabel: 'Remove item'
		});
		if (!confirmed) return;
		try {
			const response = await api.removeKitItem(kit.id, item.id);
			if (!response.success) {
				problem(response, 'Could not remove the item.');
				return;
			}
			if (response.data) adopt(response.data);
			else await load();
		} catch (caught) {
			problem(caught, 'Could not remove the item.');
		}
	}

	async function persistOrder(next: KitItem[], previous: KitItem[]) {
		if (!kit) return;
		kit = { ...kit, items: next };
		try {
			const response = await api.reorderKitItems(kit.id, next.map((item) => item.id));
			if (response.success && response.data) {
				kit = response.data;
				rememberKit(response.data);
			} else throw new Error('reorder refused');
		} catch {
			kit = { ...kit, items: previous };
			toasts.error('Could not save the new order.');
		}
	}

	function startDrag(item: KitItem, event: DragEvent) {
		dragId = item.id;
		if (event.dataTransfer) {
			event.dataTransfer.effectAllowed = 'move';
			event.dataTransfer.setData('text/plain', item.id);
		}
	}

	function endDrag() {
		dragId = null;
		dropIndex = null;
	}

	function overTile(event: DragEvent, index: number) {
		if (!dragId) return;
		event.preventDefault();
		dropIndex = index;
	}

	function dropOn(event: DragEvent, index: number) {
		if (!dragId) return;
		event.preventDefault();
		const from = items.findIndex((item) => item.id === dragId);
		endDrag();
		if (from === -1 || from === index) return;
		void persistOrder(moveItem(items, from, index), items);
	}

	function nudge(item: KitItem, step: -1 | 1) {
		const from = items.findIndex((entry) => entry.id === item.id);
		const to = from + step;
		if (from === -1 || to < 0 || to >= items.length) return;
		void persistOrder(moveItem(items, from, to), items);
	}

	async function duplicate() {
		if (!kit) return;
		working = true;
		try {
			const response = await api.duplicateKit(kit.id);
			if (response.success && response.data) {
				rememberKit(response.data);
				bumpKits();
				toasts.success(`Duplicated as ${response.data.name}`);
				onOpenKit(response.data.id);
			} else problem(response, 'Could not duplicate the kit.');
		} catch (caught) {
			problem(caught, 'Could not duplicate the kit.');
		} finally {
			working = false;
		}
	}

	async function askDelete() {
		if (!kit) return;
		let used = '';
		try {
			const response = await api.getKitUsage(kit.id);
			if (response.success && response.data) {
				const parts: string[] = [];
				if (response.data.sessions > 0) parts.push(`${response.data.sessions} saved ${response.data.sessions === 1 ? 'session' : 'sessions'}`);
				if (response.data.films > 0) parts.push(`${response.data.films} Director ${response.data.films === 1 ? 'film' : 'films'}`);
				if (parts.length > 0) used = ` It is used in ${parts.join(' and ')}. Their chips stay and are drawn as deleted.`;
			}
		} catch {
			used = '';
		}
		const confirmed = await confirmDialog({
			title: 'Delete kit?',
			message: `Delete ${kit.name} (${formatKitPath(kit.handle)})?${used} Library files stay; written text goes with the kit.`,
			variant: 'danger',
			confirmLabel: 'Delete kit'
		});
		if (!confirmed) return;
		try {
			const response = await api.deleteKit(kit.id);
			if (!response.success) {
				problem(response, 'Could not delete the kit.');
				return;
			}
			forgetKit(kit.id);
			bumpKits();
			toasts.success(`Deleted ${kit.name}`);
			onDeleted();
		} catch (caught) {
			problem(caught, 'Could not delete the kit.');
		}
	}

	onMount(() => {
		void load();
	});
</script>

<svelte:window onpaste={handlePaste} />

<input bind:this={fileInput} type="file" accept="image/*,video/*,audio/*" multiple class="hidden" onchange={handleFileChosen} />

{#if loading && !kit}
	<div class="flex justify-center py-20"><Spinner /></div>
{:else if failed || !kit}
	<div class="p-4 sm:p-6">
		<LoadErrorState title="Could not open this kit" message="It may have been deleted." onRetry={() => void load()} />
		<div class="mt-3 flex justify-center"><Button variant="secondary" size="sm" onclick={onBack}>Back to Kits</Button></div>
	</div>
{:else}
	<header class="sticky top-0 z-30 flex min-h-header flex-shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-line bg-surface-1 px-4 py-2.5 sm:px-6">
		<button type="button" class="flex items-center gap-1 text-sm font-medium text-fg-muted hover:text-fg" aria-label="Back to Kits" onclick={onBack}>
			<Icon name="chevron-left" className="h-3.5 w-3.5" />Kits
		</button>
		<span class="h-5 w-px flex-shrink-0 bg-line-strong" aria-hidden="true"></span>
		<span class="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-subtle">
			<Icon name="layers" className="h-4 w-4" />
		</span>
		<div class="min-w-0 flex-1">
			<div class="flex min-w-0 flex-wrap items-center gap-2">
				<label class="min-w-0" style="width:{Math.min(22, Math.max(7, titleDraft.length * 0.75 + 2))}rem">
					<span class="sr-only">Kit name</span>
					<input
						class="h-8 w-full rounded border border-transparent bg-transparent px-1.5 text-base font-semibold text-fg transition-colors hover:border-line-strong focus:border-signal focus:bg-field-bg focus:outline-none"
						bind:value={titleDraft}
						onblur={() => void commitTitle()}
						onkeydown={(event) => {
							if (event.key === 'Enter') (event.currentTarget as HTMLInputElement).blur();
							if (event.key === 'Escape' && kit) {
								titleDraft = kit.name;
								(event.currentTarget as HTMLInputElement).blur();
							}
						}}
					/>
				</label>
				<button
					type="button"
					class="inline-flex items-center gap-1 rounded border border-line-strong bg-surface-2 px-2 py-0.5 text-sm font-medium text-fg-muted hover:text-fg"
					aria-label="Label: {kit.label || 'none'}. Change"
					onclick={() => (showDetails = true)}
				>
					{kit.label || 'Add label'}<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
				</button>
			</div>
			<div class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 pl-1.5 text-sm">
				<KitPath handle={kit.handle} class="text-fg-muted" />
				<CopyButton text={formatKitPath(kit.handle)} size="xs" ariaLabel="Copy {formatKitPath(kit.handle)}" />
				<span class="text-fg-subtle">·</span>
				<span class="font-mono tabular-nums text-fg-subtle">{formatItemCount(items.length)}</span>
			</div>
		</div>
		<div class="ml-auto flex flex-wrap items-center justify-end gap-2 max-sm:basis-full" data-detail-actions>
			<Menu label="Add item" width={240} align="right" items={menuItems} onselect={pickSource}>
				{#snippet trigger({ toggle })}
					<Button variant="primary" size="sm" icon="plus" loading={working} onclick={toggle}>
						Add item<Icon name="chevron-down" className="h-3.5 w-3.5" />
					</Button>
				{/snippet}
			</Menu>
			<Button variant="secondary" size="sm" icon="copy" onclick={() => void duplicate()}>Duplicate</Button>
			<IconButton icon="edit" label="Name, path and label" onclick={() => (showDetails = true)} />
			<IconButton icon="trash" label="Delete kit" class="text-danger" onclick={() => void askDelete()} />
		</div>
	</header>

	<main>
		<div class="mx-auto max-w-[1240px] p-4 sm:p-6">
			<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
				{#if dragId}
					<p class="text-sm text-fg-muted">Drag to reorder. <span class="font-mono text-fg">image1</span> is the first image a model sees.</p>
				{:else}
					<p class="text-sm text-fg-muted">
						In a prompt, type <KitPath handle={kit.handle} class="text-fg" /> for everything a model can use{#if items.length > 0}, or <KitPath handle={kit.handle} item={items[0].name} class="text-fg" /> for one item{/if}.
					</p>
				{/if}
				<KitKindCounts counts={kit.counts} class="text-fg-muted" />
			</div>
			<div
				class="relative grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
				use:fileDrop={{ onFiles: (files) => void uploadFiles(Array.from(files)), onActive: (active) => (dropping = active) }}
				data-kit-items
			>
				{#each items as item, index (item.id)}
					<div
						class="relative"
						role="presentation"
						ondragover={(event) => overTile(event, index)}
						ondrop={(event) => dropOn(event, index)}
					>
						{#if dragId && dropIndex === index && dragId !== item.id}
							<span class="absolute inset-y-0 -left-[10px] z-30 w-0.5 rounded bg-signal"></span>
						{/if}
						<KitItemTile
							{item}
							handle={kit.handle}
							{taken}
							position={index + 1}
							lifted={dragId === item.id}
							onRename={renameItem}
							onRemove={removeItem}
							onPreview={(entry) => (preview = entry)}
							onEditText={(entry) => (textDialog = { item: entry })}
							onDragStart={startDrag}
							onDragEnd={endDrag}
							onNudge={nudge}
						/>
					</div>
				{/each}
				<div
					class="flex min-h-[220px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-4 text-center {dropping
						? 'border-signal bg-signal/10'
						: 'border-line-strong bg-surface-1'}"
				>
					<span class="text-sm text-fg-muted">{full ? `This kit is full (${KIT_ITEM_LIMIT})` : 'Add an item'}</span>
					{#if !full}
						<div class="flex flex-wrap items-center justify-center gap-1">
							<Button variant="ghost" size="xs" icon="grid" onclick={() => pickSource('library')}>Library</Button>
							<Button variant="ghost" size="xs" icon="upload" onclick={() => pickSource('upload')}>Upload</Button>
							<Button variant="ghost" size="xs" icon="document" onclick={() => pickSource('text')}>Text</Button>
						</div>
						<span class="font-mono text-sm text-fg-subtle">or drop files here</span>
					{/if}
				</div>
			</div>
			{#if items.length === 0}
				<div class="mt-4">
					<EmptyState compact icon="layers" title="This kit is empty" description="Add pictures, clips, sounds or a note. Each gets a name you call it by." />
				</div>
			{/if}
		</div>
	</main>

	{#if showDetails}
		<KitDetailsDialog
			{kit}
			{labels}
			onSave={saveDetails}
			onDelete={() => {
				showDetails = false;
				void askDelete();
			}}
			onClose={() => (showDetails = false)}
		/>
	{/if}

	{#if textDialog}
		<KitTextDialog
			title={textDialog.item ? `Edit ${textDialog.item.name}` : 'Write text'}
			initial={textDialog.item?.text ?? ''}
			confirmLabel={textDialog.item ? 'Save' : 'Add text'}
			onSave={saveText}
			onClose={() => (textDialog = null)}
		/>
	{/if}

	<UploadLibraryModal
		isOpen={libraryOpen}
		onClose={() => (libraryOpen = false)}
		multiple
		title="Add to {kit.name} from your Library"
		onConfirm={handleLibraryConfirm}
	/>
	<GenerationHistoryModal
		isOpen={historyOpen}
		onClose={() => (historyOpen = false)}
		multiple
		onSelect={() => {}}
		onConfirm={(_keys, picked) => void handleHistoryConfirm(picked)}
		title="Add to {kit.name} from Generation History"
	/>

	<MediaPreviewModal
		isOpen={preview !== null}
		onClose={() => (preview = null)}
		kind={preview?.kind === 'video' ? 'video' : 'image'}
		url={preview?.url ?? ''}
		label={preview?.name ?? 'Preview'}
	/>
{/if}
