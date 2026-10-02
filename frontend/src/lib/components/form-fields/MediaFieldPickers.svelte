<script lang="ts">
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import UploadLibraryModal from '$lib/components/modals/UploadLibraryModal.svelte';
	import type { UploadFileInfo } from '$lib/services/api/media';
	import type { MediaKind } from './mediaLoaderConfig';

	const KIND_TITLE: Record<MediaKind, string> = { image: 'Image', video: 'Video', audio: 'Audio' };

	let {
		historyOpen,
		libraryOpen,
		kind,
		onHistoryClose,
		onLibraryClose,
		onHistorySelect,
		onLibrarySelect
	}: {
		historyOpen: boolean;
		libraryOpen: boolean;
		kind: MediaKind | null;
		onHistoryClose: () => void;
		onLibraryClose: () => void;
		onHistorySelect: (generation: any, file: any) => void;
		onLibrarySelect: (upload: UploadFileInfo) => void;
	} = $props();

	let historyKind = $derived(kind ?? undefined);
</script>

<GenerationHistoryModal
	isOpen={historyOpen}
	onClose={onHistoryClose}
	onSelect={onHistorySelect}
	mediaType={historyKind}
	title="Select {historyKind ? KIND_TITLE[historyKind] : 'Media'} from Generation History"
/>

<UploadLibraryModal
	isOpen={libraryOpen}
	onClose={onLibraryClose}
	onSelect={onLibrarySelect}
	mediaType={kind ?? undefined}
	title="Select {kind ? KIND_TITLE[kind] : 'Media'} from Your Uploads"
/>
