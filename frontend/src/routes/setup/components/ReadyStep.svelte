<script lang="ts">
	import { Button, Card } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import StepHeader from '$lib/components/setup/StepHeader.svelte';
	import StepFacts from '$lib/components/setup/StepFacts.svelte';
	import NoCardOptions from '$lib/components/setup/NoCardOptions.svelte';
	import { NO_CARD_SENTENCE } from '$lib/utils/setupGuide';
	import type { SmokeFailure } from '$lib/utils/setupRunDisplay';

	let {
		installedName,
		thumbnailUrl,
		modelsPath,
		smokeFailure = null,
		belowCard = false,
		noCard = false,
		onCreate
	}: {
		installedName: string | null;
		thumbnailUrl: string | null;
		modelsPath: string;
		smokeFailure?: SmokeFailure | null;
		belowCard?: boolean;
		noCard?: boolean;
		onCreate: () => void;
	} = $props();

	const NEXT_STEPS = [
		{
			title: 'Add another models folder',
			body: 'Reuse models from Forge, A1111 or any other folder.',
			label: 'Admin → Models → Folders',
			href: '/admin?tab=models&view=folders'
		},
		{
			title: 'Install more starters',
			body: 'Video, music, 3D and more image models.',
			label: 'Admin → Recipes',
			href: '/admin?tab=recipes'
		}
	];

	const view = $derived<'test-failed' | 'no-card' | 'installed' | 'empty'>(
		installedName && smokeFailure
			? 'test-failed'
			: installedName
				? 'installed'
				: noCard
					? 'no-card'
					: 'empty'
	);
	const lede = $derived(
		view === 'installed'
			? `${installedName} is installed and a test image was made on this computer. Open Generate to try your own prompt.`
			: 'Your account is ready. Install a starter model any time from Setup, then generate.'
	);
	const facts = $derived([
		{ label: 'Your account lives in', body: [{ mono: 'storage/db.sqlite in your PotionUI folder' }] },
		{ label: 'Your models live in', body: [{ mono: modelsPath }] },
		{ label: 'Look again any time', body: ['This checklist stays at Setup in the sidebar.'] }
	]);
</script>

<section class="flex flex-col gap-5" aria-labelledby="h-ready">
	{#if view === 'test-failed' && smokeFailure}
		<Card class="border-warning/40 bg-warning/5">
			<div class="flex flex-col gap-3" data-ready-state="test-failed">
				<StepHeader
					step="ready"
					titleId="h-ready"
					title={`${installedName} is installed`}
					status={{ text: 'Test failed', tone: 'warning' }}
				/>
				<div class="grid grid-cols-[20px_1fr] gap-3">
					<Icon name="warning" className="mt-px h-5 w-5 text-warning" />
					<div class="flex flex-col gap-1 text-fg-muted">
						<p data-smoke-reason>{smokeFailure.reason}</p>
						{#if smokeFailure.action}
							<p>{smokeFailure.action}</p>
						{/if}
						{#if belowCard}
							<p data-below-card>This card is below what {installedName} needs.</p>
						{/if}
						{#if smokeFailure.technical}
							<details>
								<summary class="cursor-pointer list-none rounded-sm font-medium text-signal hover:underline">
									Details
								</summary>
								<p class="mt-1 break-words font-mono text-sm text-fg-subtle">{smokeFailure.technical}</p>
							</details>
						{/if}
					</div>
				</div>
				<div class="flex flex-wrap items-center gap-4">
					<Button variant="primary" size="lg" class="max-sm:w-full" onclick={onCreate}>
						Try it in Generate
					</Button>
					<a class="font-medium text-signal hover:underline" href="/admin?tab=recipes">Run the check again</a>
				</div>
			</div>
		</Card>
	{:else if view === 'no-card'}
		<div class="flex flex-col gap-3" data-ready-state="no-card">
			<StepHeader
				step="ready"
				titleId="h-ready"
				title="Your account is ready"
				lede={NO_CARD_SENTENCE}
				status={{ text: 'One more thing', tone: 'warning' }}
			/>
			<NoCardOptions />
			<div>
				<Button variant="primary" size="lg" class="max-sm:w-full" href="/admin?tab=backends">
					Connect a backend
				</Button>
			</div>
		</div>
	{:else}
		<Card class="border-success/45 bg-success/5">
			<div class="grid grid-cols-[auto_1fr] items-center gap-5 max-sm:grid-cols-1" data-ready-state={view}>
				{#if thumbnailUrl}
					<img
						src={thumbnailUrl}
						alt="Output from your setup's test generation"
						class="aspect-square w-full max-w-[200px] rounded-lg border border-line-strong bg-black object-cover"
					/>
				{:else}
					<div
						class="grid aspect-square w-full max-w-[200px] place-items-center rounded-lg border border-line-strong bg-black"
						aria-hidden="true"
					>
						<span class="px-6 text-center font-mono text-sm uppercase tracking-[0.06em] text-white/60">
							First image
						</span>
					</div>
				{/if}
				<div class="flex flex-col gap-3">
					<StepHeader
						step="ready"
						titleId="h-ready"
						title="You're all set"
						{lede}
						status={{ text: 'Done', tone: 'success' }}
					/>
					<div>
						<Button variant="primary" size="lg" class="max-sm:w-full" onclick={onCreate}>
							Create your first image
						</Button>
					</div>
				</div>
			</div>
		</Card>
	{/if}

	{#if view !== 'no-card'}
		<div>
			<p class="mb-2 font-mono text-sm uppercase tracking-[0.06em] text-fg-subtle">Optional next steps</p>
			<Card padding="none" class="overflow-hidden">
				{#each NEXT_STEPS as item, i (item.title)}
					<div
						class="grid grid-cols-[1fr_auto] items-center gap-3 px-4 py-3 max-sm:grid-cols-1 {i > 0
							? 'border-t border-line'
							: ''}"
					>
						<div>
							<div class="font-medium text-fg">{item.title}</div>
							<div class="text-fg-muted">{item.body}</div>
						</div>
						<a class="font-medium text-signal hover:underline" href={item.href}>{item.label}</a>
					</div>
				{/each}
			</Card>
		</div>
	{/if}

	<StepFacts {facts} />
</section>
