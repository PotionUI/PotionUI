<script lang="ts">
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import UploadLibraryModal from '$lib/components/modals/UploadLibraryModal.svelte';
	import type { UploadFileInfo } from '$lib/services/api/media';
	import type { PickedMedia } from '$lib/components/modals/mediaPickerSelection';
	import type { MediaKind } from './mediaLoaderConfig';

	const KIND_TITLE: Record<MediaKind, string> = { image: 'Image', video: 'Video', audio: 'Audio' };

	let {
		historyOpen,
		libraryOpen,
		kind,
		onHistoryClose,
		onLibraryClose,
		onHistorySelect,
		onLibrarySelect,
		historyMultiple = false,
		replacing = false,
		onHistoryConfirm
	}: {
		historyOpen: boolean;
		libraryOpen: boolean;
		kind: MediaKind | null;
		onHistoryClose: () => void;
		onLibraryClose: () => void;
		onHistorySelect: (generation: any, file: any) => void;
		onLibrarySelect: (upload: UploadFileInfo) => void;
		historyMultiple?: boolean;
		replacing?: boolean;
		onHistoryConfirm?: (items: PickedMedia[]) => void;
	} = $props();

	let historyKind = $derived(kind ?? undefined);
	let noun = $derived(historyKind ? KIND_TITLE[historyKind] : 'Media');
	let historyTitle = $derived(replacing ? `Replace ${noun.toLowerCase()}` : `Select ${noun} from Generation History`);
	let libraryTitle = $derived(replacing ? `Replace ${noun.toLowerCase()}` : `Select ${noun} from Your Uploads`);
</script>

<GenerationHistoryModal
	isOpen={historyOpen}
	onClose={onHistoryClose}
	onSelect={onHistorySelect}
	multiple={historyMultiple}
	onConfirm={(_keys, items) => onHistoryConfirm?.(items)}
	mediaType={historyKind}
	title={historyTitle}
/>

<UploadLibraryModal
	isOpen={libraryOpen}
	onClose={onLibraryClose}
	onSelect={onLibrarySelect}
	mediaType={kind ?? undefined}
	title={libraryTitle}
/>
