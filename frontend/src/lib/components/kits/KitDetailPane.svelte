<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import MediaPickerModal from '$lib/components/modals/mediaPicker/MediaPickerModal.svelte';
	import { libraryItemOf } from '$lib/components/modals/mediaPicker/pickedMedia';
	import type { PickerSourceId } from '$lib/components/modals/mediaPicker/pickerModel';
	import MediaPreviewModal from '$lib/components/modals/MediaPreviewModal.svelte';
	import type { PickedMedia } from '$lib/components/modals/mediaPickerSelection';
	import { fileDrop, readPastedImage } from '$lib/components/form-fields/mediaFieldInput';
	import { Button, CopyButton, LoadErrorState, Menu, Spinner } from '$lib/components/ui';
	import { DetailHeader } from '$lib/components/detail';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import { api } from '$lib/services/api/index';
	import { confirmDialog } from '$lib/stores/confirm';
	import { bumpKits, forgetKit, rememberKit } from '$lib/stores/kits';
	import { toasts } from '$lib/stores/toast';
	import { primaryModifier } from '$lib/utils/platform';
	import { moveItem } from '$lib/components/form-fields/mediaLoaderReorder';
	import { isNameClash, kitErrorMessage, kitErrorSuggestion } from '$lib/kits/kitErrors';
	import { kindOfMediaType, describeLimit, picksFromHistoryFiles, picksFromLibraryItems, picksNames, takenNames, toNewItems, type KitPick } from '$lib/kits/kitPicks';
	import { formatItemCount } from '$lib/kits/kitCounts';
	import { formatKitPath } from '$lib/kits/kitPath';
	import { defaultItemName } from '$lib/kits/kitNaming';
	import { layoutJustifiedRows, flattenJustifiedRows } from '$lib/utils/justifiedLayout';
	import { KIT_ITEM_LIMIT, type KitDetail, type KitItem, type KitLabelCount, type KitPatch, type NewKitItem } from '$lib/kits/types';
	import KitDetailsDialog from './KitDetailsDialog.svelte';
	import KitItemTile from './KitItemTile.svelte';
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
	let picking = $state<PickerSourceId | null>(null);
	let preview = $state<KitItem | null>(null);
	let dragId = $state<string | null>(null);
	let dropIndex = $state<number | null>(null);
	let dropping = $state(false);
	let working = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	const modifier = primaryModifier();

	let items = $derived(kit?.items ?? []);

	const WALL_GAP = 12;
	const MIN_TILE_WIDTH = 150;
	let wallWidth = $state(0);
	let wallTarget = $derived(Math.round(Math.max(180, Math.min(320, (wallWidth > 0 ? wallWidth : 1200) / 4.6))));

	function tileAspect(item: KitItem): number {
		if (item.missing) return 1;
		if (item.kind === 'image' || item.kind === 'video') {
			const ratio = item.width && item.height ? item.width / item.height : 1.5;
			return Math.max(MIN_TILE_WIDTH / wallTarget, ratio);
		}
		return 1.3;
	}

	let wall = $derived.by(() => {
		const width = wallWidth > 0 ? wallWidth : 1200;
		const flat = flattenJustifiedRows(
			layoutJustifiedRows(
				[
					...items.map((item) => ({ item: item as KitItem | null, aspect: tileAspect(item) })),
					{ item: null as KitItem | null, aspect: 1.3 }
				],
				width,
				wallTarget,
				WALL_GAP
			)
		);
		let lastStart = flat.length - 1;
		while (lastStart > 0 && !flat[lastStart - 1].rowEnd) lastStart -= 1;
		const lastRow = flat.slice(lastStart);
		const used = lastRow.reduce((sum, box) => sum + box.width, 0) + WALL_GAP * (lastRow.length - 1);
		if (used >= width - 2) return flat;
		return flat.map((box, index) => (index >= lastStart ? { ...box, rowEnd: false } : box));
	});
	let taken = $derived(takenNames(items));
	let full = $derived(items.length >= KIT_ITEM_LIMIT);

	const menuItems: MenuItem[] = [
		{ id: 'library', label: 'Library', icon: 'photo' },
		{ id: 'history', label: 'History', icon: 'clock' },
		{ id: 'upload', label: 'Upload file', icon: 'upload' },
		{ id: 'paste', label: 'Paste', icon: 'clipboard-list' },
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

	function handlePickerConfirm(_keys: string[], picked: PickedMedia[]) {
		const libraryItems = picked.map(libraryItemOf).filter((entry): entry is NonNullable<typeof entry> => !!entry);
		const files = picked.map((entry) => entry.file).filter((entry): entry is NonNullable<typeof entry> => !!entry);
		void addItems([
			...picksFromLibraryItems(libraryItems),
			...picksFromHistoryFiles(files, (fileId) => api.getFileURL(fileId, 'small'))
		]);
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
		if (id === 'library' || id === 'history') picking = id;
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
	{@const current = kit}
	<div class="sticky top-0 z-30">
		<DetailHeader title={current.name} icon="layers" backLabel="Kits" {onBack}>
			{#snippet titleEditor()}
				<label class="min-w-0" style="width:{Math.min(22, Math.max(7, titleDraft.length * 0.75 + 2))}rem">
					<span class="sr-only">Kit name</span>
					<input
						class="h-7 w-full rounded border border-transparent bg-transparent px-1.5 text-base font-semibold text-fg transition-colors hover:border-line-strong focus:border-signal focus:bg-field-bg focus:outline-none"
						bind:value={titleDraft}
						onblur={() => void commitTitle()}
						onkeydown={(event) => {
							if (event.key === 'Enter') (event.currentTarget as HTMLInputElement).blur();
							if (event.key === 'Escape') {
								titleDraft = current.name;
								(event.currentTarget as HTMLInputElement).blur();
							}
						}}
					/>
				</label>
			{/snippet}
			{#snippet subtitle()}
				<span class="flex flex-wrap items-center gap-x-2 pl-1.5 text-sm">
					<KitPath handle={current.handle} class="text-fg-muted" />
					<CopyButton text={formatKitPath(current.handle)} size="xs" ariaLabel="Copy {formatKitPath(current.handle)}" />
					<span class="text-fg-subtle">·</span>
					<span class="tabular-nums text-fg-subtle">{formatItemCount(items.length)}</span>
				</span>
			{/snippet}
			{#snippet actions()}
				<button
					type="button"
					class="inline-flex h-8 flex-none items-center gap-1.5 whitespace-nowrap rounded border border-line-strong bg-surface-2/50 pl-2.5 pr-2 text-sm text-fg transition-colors hover:bg-surface-3/60 focus-visible:ring-2 focus-visible:ring-signal {current.label
						? 'border-signal/25 bg-signal/10 text-signal'
						: ''}"
					aria-label="Label: {current.label || 'none'}. Change"
					onclick={() => (showDetails = true)}
				>
					<span class={current.label ? '' : 'text-fg-subtle'}>Label</span>
					<span class="max-w-[10rem] truncate">{current.label || 'None'}</span>
					<Icon name="chevron-down" className="h-3 w-3 flex-none text-fg-subtle" />
				</button>
				<Menu label="Add item" width={240} align="right" items={menuItems} onselect={pickSource}>
					{#snippet trigger({ toggle })}
						<Button variant="primary" size="sm" icon="plus" class="h-8" loading={working} onclick={toggle}>
							Add item<Icon name="chevron-down" className="h-3.5 w-3.5" />
						</Button>
					{/snippet}
				</Menu>
				<Menu
					label="More actions"
					tooltip="More actions"
					width={220}
					align="right"
					items={[
						{ id: 'edit', label: 'Name, path and label', icon: 'edit' },
						{ id: 'duplicate', label: 'Duplicate', icon: 'copy' },
						{ id: 'separator', label: '', separator: true },
						{ id: 'delete', label: 'Delete kit', icon: 'trash', danger: true }
					]}
					onselect={(id) => {
						if (id === 'edit') showDetails = true;
						else if (id === 'duplicate') void duplicate();
						else if (id === 'delete') void askDelete();
					}}
				/>
			{/snippet}
		</DetailHeader>
	</div>

	<main>
		<div class="px-3 py-3 md:px-6 md:py-6">
			<div
				class="relative flex flex-wrap items-stretch"
				style="gap: {WALL_GAP}px"
				bind:clientWidth={wallWidth}
				use:fileDrop={{ onFiles: (files) => void uploadFiles(Array.from(files)), onActive: (active) => (dropping = active) }}
				data-kit-items
			>
				{#each wall as box, index (box.item?.id ?? '__add__')}
					{@const item = box.item}
					<div
						class="relative flex flex-col"
						style="flex: {box.rowEnd ? '1 1' : '0 0'} {box.width}px; min-width: 0"
						role="presentation"
						ondragover={(event) => item && overTile(event, index)}
						ondrop={(event) => item && dropOn(event, index)}
					>
						{#if item}
							{#if dragId && dropIndex === index && dragId !== item.id}
								<span class="absolute inset-y-0 -left-[7px] z-30 w-0.5 rounded bg-signal"></span>
							{/if}
							<KitItemTile
								{item}
								handle={kit.handle}
								{taken}
								position={index + 1}
								lifted={dragId === item.id}
								mediaHeight={box.height}
								onRename={renameItem}
								onRemove={removeItem}
								onPreview={(entry) => (preview = entry)}
								onEditText={(entry) => (textDialog = { item: entry })}
								onDragStart={startDrag}
								onDragEnd={endDrag}
								onNudge={nudge}
							/>
						{:else}
							<div
								class="flex flex-1 flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-4 text-center {dropping
									? 'border-signal bg-signal/10'
									: 'border-line-strong bg-surface-1'}"
								style="min-height: {Math.round(box.height)}px"
							>
								<span class="text-sm text-fg-muted">{full ? `This kit is full (${KIT_ITEM_LIMIT})` : 'Add an item'}</span>
								{#if !full}
									<div class="flex flex-wrap items-center justify-center gap-1">
										<Button variant="ghost" size="xs" icon="grid" onclick={() => pickSource('library')}>Library</Button>
										<Button variant="ghost" size="xs" icon="clock" onclick={() => pickSource('history')}>History</Button>
										<Button variant="ghost" size="xs" icon="upload" onclick={() => pickSource('upload')}>Upload</Button>
										<Button variant="ghost" size="xs" icon="document" onclick={() => pickSource('text')}>Text</Button>
									</div>
									<span class="font-mono text-sm text-fg-subtle">or drop files here</span>
								{/if}
							</div>
						{/if}
					</div>
				{/each}
			</div>
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

	<MediaPickerModal
		isOpen={picking !== null}
		onClose={() => (picking = null)}
		sources={['history', 'library', 'kits']}
		initialSource={picking ?? 'library'}
		mediaTypes={['image', 'video', 'audio']}
		multiple
		title="Add to {kit.name}"
		confirmLabel="Add to kit"
		onConfirm={handlePickerConfirm}
	/>

	<MediaPreviewModal
		isOpen={preview !== null}
		onClose={() => (preview = null)}
		kind={preview?.kind === 'video' ? 'video' : 'image'}
		url={preview?.url ?? ''}
		label={preview?.name ?? 'Preview'}
	/>
{/if}
