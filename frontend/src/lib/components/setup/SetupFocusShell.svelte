<script lang="ts">
	import type { Snippet } from 'svelte';
	import Logo from '$lib/components/brand/Logo.svelte';
	import SetupStepper from './SetupStepper.svelte';
	import TrustFooter from './TrustFooter.svelte';
	import type { SetupStepId } from '$lib/utils/setupGuide';
	import { resetScrollOnChange } from '$lib/utils/scrollToTop';

	let {
		current,
		onnavigate,
		children
	}: {
		current: SetupStepId;
		onnavigate?: (id: SetupStepId) => void;
		children?: Snippet;
	} = $props();

</script>

<div use:resetScrollOnChange={current} class="flex min-h-screen flex-col bg-canvas text-fg">
	<header
		class="flex min-h-header items-center gap-3 border-b border-line bg-surface-1 px-6 py-3 max-sm:px-4"
	>
		<Logo size={30} />
		<div class="min-w-0">
			<p class="text-lg font-semibold leading-5 text-fg">Set up PotionUI</p>
			<p class="text-base text-fg-subtle max-sm:hidden">
				Six short steps. Nothing downloads without your approval.
			</p>
		</div>
	</header>

	<SetupStepper {current} ownerExists={false} {onnavigate} />

	<main
		class="mx-auto flex w-full max-w-[720px] flex-1 flex-col gap-5 px-6 pb-12 pt-8 max-sm:gap-4 max-sm:px-4 max-sm:pb-10 max-sm:pt-5"
	>
		{@render children?.()}
	</main>

	<TrustFooter />
</div>
