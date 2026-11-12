<script lang="ts">
	import type { SetupMachine, SetupRecipe, SetupRun } from '$lib/services/api/setup';
	import { Card } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import StepHeader from '$lib/components/setup/StepHeader.svelte';
	import StepFacts from '$lib/components/setup/StepFacts.svelte';
	import RecipeRunProgress from '$lib/components/recipes/RecipeRunProgress.svelte';
	import { setupRunActions } from '$lib/components/recipes/runActions';
	import { runPhase, shortfallSentence, starterShortfall, stepStatus } from '$lib/utils/setupGuide';

	let {
		run,
		recipe,
		machine = null,
		fetchError,
		modelsPath,
		onRunUpdated,
		onStartOver
	}: {
		run: SetupRun;
		recipe: SetupRecipe | null;
		machine?: SetupMachine | null;
		fetchError: string;
		modelsPath: string;
		onRunUpdated: (run: SetupRun) => void;
		onStartOver: () => void;
	} = $props();

	const phase = $derived(runPhase(run));
	const name = $derived(recipe?.name ?? 'This starter');
	const source = $derived(recipe && recipe.sources.length > 0 ? recipe.sources.join(', ') : 'the model source');
	const interrupted = $derived(run.error_code === 'RUN_INTERRUPTED');
	const shortfall = $derived(recipe ? starterShortfall(recipe, machine) : null);
	const heading = $derived(
		phase === 'consent'
			? { title: 'Review the download', lede: `${name} needs these files. Check the list, then approve. Nothing has been downloaded yet.` }
			: phase === 'failed' && interrupted
				? { title: 'Setup was interrupted', lede: 'Files already downloaded are kept. Continue to pick up where it stopped.' }
				: phase === 'failed'
					? { title: "Setup couldn't finish", lede: 'Nothing is lost. You can try again or start over with a different starter.' }
					: { title: `Installing ${name}`, lede: 'This runs on its own. The download is the long part.' }
	);
	const consentFacts = $derived([
		{ label: 'Downloaded from', body: [{ mono: source }, ' (repository under each file)'] },
		{ label: 'Saved to', body: [{ mono: modelsPath }] },
		{
			label: 'What the source sees',
			body: [
				'Hugging Face receives the download requests, and your key if you add one. Your prompts and images are not sent.'
			]
		}
	]);
</script>

<section class="flex flex-col gap-5" aria-labelledby="h-install">
	<StepHeader
		step="install"
		titleId="h-install"
		title={heading.title}
		lede={heading.lede}
		status={stepStatus('install', run)}
	/>

	<Card>
		<RecipeRunProgress
			{run}
			title={phase === 'consent' ? 'Needs your go-ahead' : 'Setting up'}
			{fetchError}
			actions={setupRunActions}
			{onRunUpdated}
			{onStartOver}
			overallBar={phase !== 'consent'}
			guided
			{consentNote}
		/>
	</Card>

	{#if phase === 'active'}
		<div class="grid grid-cols-[20px_1fr] gap-3 rounded-lg border border-line-strong bg-surface-2 px-3.5 py-3" role="note">
			<Icon name="clock" className="mt-px h-5 w-5 text-fg-muted" />
			<p class="text-fg-muted">
				<strong class="font-semibold text-fg">You can close this tab.</strong> Setup keeps running and picks
				up here when you come back to Setup.
			</p>
		</div>
	{/if}
</section>

{#snippet consentNote()}
	{#if shortfall}
		<div
			class="grid grid-cols-[20px_1fr] gap-3 rounded-lg border border-warning/40 bg-warning/5 px-3.5 py-3"
			role="note"
			data-shortfall-note
		>
			<Icon name="warning" className="mt-px h-5 w-5 text-warning" />
			<p class="text-fg-muted">{shortfallSentence(shortfall)} It will probably not run here.</p>
		</div>
	{/if}
	<p class="text-sm text-fg-muted" data-hf-info>
		Downloads come from Hugging Face (huggingface.co), a public site where people share AI models.
	</p>
	<StepFacts facts={consentFacts} />
{/snippet}
