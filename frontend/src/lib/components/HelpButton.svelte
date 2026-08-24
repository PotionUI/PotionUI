<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import TutorialModal from '$lib/components/modals/TutorialModal.svelte';
	import type { TutorialSection } from '$lib/types/tutorial';

	export let tutorialContent: TutorialSection[] = [];
	export let title: string = 'Help';

	let isModalOpen = false;

	function openModal() {
		isModalOpen = true;
	}

	function closeModal() {
		isModalOpen = false;
	}
</script>

<button
	type="button"
	class="text-fg-muted hover:text-fg transition-colors p-1.5 rounded hover:bg-surface-3/50"
	on:click={openModal}
	aria-label={`Open ${title} help`}
	title={title}
>
	<Icon name="information-circle" className="w-5 h-5" />
</button>

<TutorialModal
	isOpen={isModalOpen}
	sections={tutorialContent}
	{title}
	on:close={closeModal}
/>
