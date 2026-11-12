<script lang="ts">
	import { Button } from '$lib/components/ui';
	import StepHeader from '$lib/components/setup/StepHeader.svelte';
	import StepFacts from '$lib/components/setup/StepFacts.svelte';

	let { onStart }: { onStart: () => void } = $props();

	const FACTS = [
		{
			label: 'You will need',
			body: ['An NVIDIA graphics card with 8 GB of memory or more (GTX 16-series, RTX 20-series or newer).']
		},
		{
			label: 'Disk space',
			body: [
				'A few GB for the smallest image starter, tens of GB for the largest. You see the exact size before anything downloads.'
			]
		},
		{ label: 'Internet', body: ['Only for the model download that setup asks you to approve.'] }
	];
</script>

<section class="flex flex-col gap-5" aria-labelledby="h-welcome">
	<StepHeader
		step="welcome"
		titleId="h-welcome"
		title="A local studio for AI images and video"
		lede="PotionUI runs on this computer and uses its graphics card to make pictures and video."
	/>

	<StepFacts facts={FACTS} />

	<div class="flex items-center gap-3">
		<Button variant="primary" size="lg" class="max-sm:w-full" onclick={onStart}>Get started</Button>
	</div>
</section>
