<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import { Badge, Button } from '$lib/components/ui';
	import { libraryStore, LIBRARY_TAG_TYPE } from '$lib/stores/library';
	import { formatBytes, formatSeconds } from '$lib/utils/format';
	import { libraryItemDisplayName, libraryItemIcon } from '$lib/library/libraryItemMeta';
	import MediaEditors from '$lib/media/editors/MediaEditors.svelte';
	import {
		hasEditor,
		editorTitle,
		RESOURCE_EDIT_TOOLS,
		type MediaEditorKind,
		type MediaEditorRequest,
		type MediaEditorResult,
		type EditorMediaKind
	} from '$lib/media/editors';
	import { toasts } from '$lib/stores/toast';
	import type { LibraryItem } from '$lib/services/api/library';

	export let item: LibraryItem;
	export let onClose: () => void;
	export let onDeleteRequest: (item: LibraryItem) => void;

	$: displayName = libraryItemDisplayName(item);
	$: kind = (item.media_type ?? '').toLowerCase();
	$: selectedTagIds = item.tags.map((tag) => tag.id);
	$: audioTracks = [{ type: 'mixed' as const, url: item.url, duration: item.duration_seconds }];

	$: details = [
		item.width && item.height ? { label: 'Dimensions', value: `${item.width}×${item.height}` } : null,
		typeof item.duration_seconds === 'number'
			? { label: 'Duration', value: formatSeconds(item.duration_seconds) }
			: null,
		typeof item.fps === 'number' && item.fps > 0
			? { label: 'FPS', value: String(Math.round(item.fps)) }
			: null,
		typeof item.size === 'number' && item.size > 0
			? { label: 'Size', value: formatBytes(item.size) }
			: null,
		item.created_at
			? { label: 'Added', value: new Date(item.created_at).toLocaleString() }
			: null,
		item.mime_type ? { label: 'Type', value: item.mime_type } : null
	].filter((entry): entry is { label: string; value: string } => entry !== null);

	function handleDownload() {
		const link = document.createElement('a');
		link.href = item.url;
		link.download = displayName;
		link.click();
	}

	async function handleTagsChange(event: CustomEvent<string[]>) {
		await libraryStore.setItemTags(item.id, event.detail);
	}

	// --- Editing -----------------------------------------------------------
	// The same surfaces the MediaLoader field opens, on the row this modal is
	// already showing - so no lookup, and the replace-in-place that keeps the
	// item's tags and collections is available immediately. The mask editor is
	// not offered here: a mask is a generation input bound to a form field, not
	// a property of a stored resource.

	let editorRequest: MediaEditorRequest | null = null;

	$: editorKind = (kind === 'audio' || kind === 'video' || kind === 'image'
		? kind
		: null) as EditorMediaKind | null;

	$: editTools = RESOURCE_EDIT_TOOLS.filter((tool) => hasEditor(tool.key, editorKind)).map((tool) => ({
		...tool,
		title: editorTitle(tool.key, editorKind as EditorMediaKind)
	}));

	function openEditor(tool: MediaEditorKind) {
		if (!editorKind) return;
		editorRequest = {
			kind: tool,
			source: {
				url: item.url,
				kind: editorKind,
				fileName: displayName,
				itemId: item.id,
				storedPath: `uploads/${item.filename}`,
				width: item.width ?? null,
				height: item.height ?? null,
				durationSeconds: item.duration_seconds ?? null,
				fps: item.fps ?? null
			},
			itemIndex: null
		};
	}

	async function handleEditorResult(result: MediaEditorResult) {
		if (result.type === 'items') {
			// A split leaves the source alone and adds its parts alongside it, so
			// there is no row here to patch - only newer ones to bring into view.
			await libraryStore.showNewRows();
			toasts.success(
				`Split into ${result.items.length} parts — the original is untouched`
			);
			onClose();
			return;
		}
		if (result.type !== 'item') return;
		await libraryStore.applyEditResult(result.item, result.replaced);
		toasts.success(
			result.replaced ? 'Replaced — tags and collections kept' : 'Saved as a new library item'
		);
	}
</script>

<BaseModal isOpen={true} size="xl" title={displayName} on:close={onClose}>
	<div class="p-4 md:p-6 flex flex-col gap-4">
		<div class="rounded-lg border border-line-strong bg-black overflow-hidden flex items-center justify-center min-h-[16rem] max-h-[60vh]">
			{#if kind === 'video'}
				<video src={item.url} class="max-h-[60vh] max-w-full" controls playsinline>
					<track kind="captions" />
				</video>
			{:else if kind === 'audio'}
				<div class="w-full p-6 bg-surface-1">
					<AudioPlayer tracks={audioTracks} showWaveform={true} showDownload={false} />
				</div>
			{:else}
				<img src={item.url} alt={displayName} class="max-h-[60vh] max-w-full object-contain" />
			{/if}
		</div>

		<div class="flex flex-wrap items-center gap-2">
			<Badge variant="neutral" class="inline-flex items-center gap-1.5">
				<Icon name={libraryItemIcon(item.media_type)} className="w-3 h-3" />
				{kind || 'media'}
			</Badge>
			<div class="flex-1"></div>
			<TagSelector
				{selectedTagIds}
				tagType={LIBRARY_TAG_TYPE}
				allowCreate={true}
				triggerStyle="pills"
				placeholder="Add tags..."
				on:change={handleTagsChange}
			/>
		</div>

		{#if details.length > 0}
			<dl class="grid grid-cols-2 md:grid-cols-3 gap-3">
				{#each details as detail (detail.label)}
					<div class="rounded border border-line bg-surface-2/50 px-3 py-2 min-w-0">
						<dt class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
							{detail.label}
						</dt>
						<dd class="mt-0.5 font-mono tabular-nums text-xs text-fg truncate" title={detail.value}>
							{detail.value}
						</dd>
					</div>
				{/each}
			</dl>
		{/if}

		<div class="flex flex-wrap items-center gap-2 pt-2 border-t border-line">
			{#each editTools as tool (tool.key)}
				<Button
					variant="secondary"
					size="sm"
					icon={tool.icon}
					title={tool.title}
					onclick={() => openEditor(tool.key)}
				>
					{tool.label}
				</Button>
			{/each}

			<div class="flex-1"></div>

			<Button variant="danger" size="sm" icon="trash" onclick={() => onDeleteRequest(item)}>
				Delete
			</Button>
			<Button variant="secondary" size="sm" icon="download" onclick={handleDownload}>
				Download
			</Button>
		</div>
	</div>
</BaseModal>

<MediaEditors
	request={editorRequest}
	onClose={() => (editorRequest = null)}
	onResult={handleEditorResult}
/>
