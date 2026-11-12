<script lang="ts">
	import MediaPickerModal from '$lib/components/modals/mediaPicker/MediaPickerModal.svelte';
	import { routePicked } from '$lib/components/modals/mediaPicker/pickedMedia';
	import type { PickerSourceId } from '$lib/components/modals/mediaPicker/pickerModel';
	import type { PickedMedia } from '$lib/components/modals/mediaPickerSelection';
	import type { LibraryItem } from '$lib/services/api/library';
	import { historyFileRef, libraryItemRef } from '$lib/tool-tabs/image/sourceRefs';
	import type { SourceEntryId } from '$lib/tool-tabs/sourceEntries';
	import type { GenerationFile } from '$lib/types/history';
	import type { MediaRef } from '$lib/types/tabs';

	let {
		onpick,
		onfile
	}: {
		onpick: (source: MediaRef) => void;
		onfile: (file: File) => void;
	} = $props();

	let picking = $state<PickerSourceId | null>(null);
	let input = $state<HTMLInputElement>();

	export function open(id: SourceEntryId) {
		if (id === 'history' || id === 'library' || id === 'kits') picking = id;
		else input?.click();
	}

	function fromHistory(_generation: unknown, file: GenerationFile) {
		picking = null;
		if (file?.file_path) onpick(historyFileRef(file));
	}

	function fromLibrary(item: LibraryItem) {
		picking = null;
		onpick(libraryItemRef(item));
	}

	function picked(media: PickedMedia) {
		routePicked(media, { history: fromHistory, library: fromLibrary });
	}

	function browse(event: Event) {
		const target = event.currentTarget as HTMLInputElement;
		const file = target.files?.[0];
		target.value = '';
		if (file) onfile(file);
	}
</script>

<input bind:this={input} type="file" accept="image/*" class="hidden" onchange={browse} data-image-browse />
<MediaPickerModal
	isOpen={picking !== null}
	onClose={() => (picking = null)}
	sources={['history', 'library', 'kits']}
	initialSource={picking ?? 'history'}
	mediaTypes={['image']}
	onPick={picked}
/>
