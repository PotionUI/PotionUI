<script lang="ts">
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import UploadLibraryModal from '$lib/components/modals/UploadLibraryModal.svelte';
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

	let showHistory = $state(false);
	let showLibrary = $state(false);
	let input = $state<HTMLInputElement>();

	export function open(id: SourceEntryId) {
		if (id === 'history') showHistory = true;
		else if (id === 'library') showLibrary = true;
		else input?.click();
	}

	function fromHistory(_generation: unknown, file: GenerationFile) {
		showHistory = false;
		if (file?.file_path) onpick(historyFileRef(file));
	}

	function fromLibrary(item: LibraryItem) {
		showLibrary = false;
		onpick(libraryItemRef(item));
	}

	function browse(event: Event) {
		const target = event.currentTarget as HTMLInputElement;
		const file = target.files?.[0];
		target.value = '';
		if (file) onfile(file);
	}
</script>

<input bind:this={input} type="file" accept="image/*" class="hidden" onchange={browse} data-image-browse />
<GenerationHistoryModal isOpen={showHistory} onClose={() => (showHistory = false)} onSelect={fromHistory} mediaType="image" title="Select an image from History" />
<UploadLibraryModal isOpen={showLibrary} onClose={() => (showLibrary = false)} onSelect={fromLibrary} mediaType="image" title="Select an image from your Library" />
