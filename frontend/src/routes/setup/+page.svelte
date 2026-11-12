<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { resetScrollOnChange } from '$lib/utils/scrollToTop';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { api } from '$lib/services/api/index';
	import type { ReadinessReport, SetupMachine, SetupRecipe, SetupRun } from '$lib/services/api/setup';
	import {
		readinessCheckBadgeVariant,
		readinessCheckStatusLabel,
		readinessAreaLabel,
		readinessAdminLink,
		readinessHeadline
	} from '$lib/utils/readinessDisplay';
	import {
		decideRunDiscovery,
		extractGenerationHandoff,
		extractSmokeFailure,
		extractSmokeGeneration,
		newestFinishedOnboardingRun
	} from '$lib/utils/setupRunDisplay';
	import {
		SKIP_TOAST_DURATION_MS,
		deriveCurrentStep,
		hasLocalCard,
		runAcknowledgedShortfall,
		showsSkipGuide,
		skipToastMessage,
		type SetupStepId
	} from '$lib/utils/setupGuide';
	import { COMPUTER_SEEN_KEY, readGuideFlag, writeGuideFlag } from '$lib/utils/setupGuideFlags';
	import { notifySetupCompleted } from '$lib/stores/setupCompletion';
	import { Badge, Button, Card, EmptyState, PageHeader, Spinner } from '$lib/components/ui';
	import { RecipeRunSession } from '$lib/components/recipes/recipeRunSession.svelte';
	import { createSetupRunHooks } from '$lib/components/recipes/setupRunHooks';
	import SetupStepper from '$lib/components/setup/SetupStepper.svelte';
	import TrustFooter from '$lib/components/setup/TrustFooter.svelte';
	import ComputerStep from './components/ComputerStep.svelte';
	import StarterStep from './components/StarterStep.svelte';
	import InstallStep from './components/InstallStep.svelte';
	import ReadyStep from './components/ReadyStep.svelte';

	const RUN_STORAGE_KEY = 'potionui:setup:activeRunId';
	const DEFAULT_MODELS_PATH = 'models/ (default location in this install)';

	const isAdmin = $derived($authStore.user?.account_type === 'ADMIN');

	let report = $state<ReadinessReport | null>(null);
	let loading = $state(true);
	let refreshing = $state(false);
	let loadError = $state('');

	let run = $state<SetupRun | null>(null);
	let runChecked = $state(false);
	let runFetchError = $state('');

	let recipes = $state<SetupRecipe[] | null>(null);
	let recipesLoading = $state(false);
	let recipesAttempted = false;
	let recipesError = $state('');
	let starting = $state(false);
	let startError = $state('');

	let machine = $state<SetupMachine | null>(null);
	let machineLoading = $state(true);

	let flagsRead = $state(false);
	let computerSeen = $state(false);
	let override = $state<SetupStepId | null>(null);

	const session = new RecipeRunSession({
		api: { get: (runId) => api.getSetupRun(runId) },
		...createSetupRunHooks({
			setRun: (next) => (run = next),
			setFetchError: (message) => (runFetchError = message),
			remember: rememberRunId,
			forget: forgetRunId
		})
	});

	const derivedStep = $derived(
		deriveCurrentStep({
			ownerExists: true,
			welcomeSeen: true,
			computerSeen,
			run,
			instanceReady: report?.overall === 'ready'
		})
	);
	const step = $derived(override ?? derivedStep);
	const guideReady = $derived(isAdmin && flagsRead && runChecked && !loading);
	const recipe = $derived(recipes?.find((r) => r.id === run?.recipe_id) ?? null);
	const modelsPath = $derived(machine?.models_disk?.path ?? DEFAULT_MODELS_PATH);
	const generationHandoff = $derived(run ? extractGenerationHandoff(run) : null);
	const smokeResult = $derived(run ? extractSmokeGeneration(run) : null);
	const generationProven = $derived(
		report?.checks.find((check) => check.area === 'generation_proven')?.status === 'ready'
	);
	const smokeFailure = $derived(run && !generationProven ? extractSmokeFailure(run) : null);
	const belowCard = $derived(runAcknowledgedShortfall(run));
	const noCard = $derived(machine !== null && !hasLocalCard(machine));
	const smokeThumbnailUrl = $derived(
		smokeResult?.filename && smokeResult.generationId
			? api.getGenerationThumbnailURL(smokeResult.generationId, smokeResult.filename, 'medium')
			: null
	);
	const installedName = $derived(
		run?.status === 'completed' && recipe ? (recipe.presets[0]?.name ?? recipe.name) : null
	);

	let notifiedCompletion = false;
	$effect(() => {
		if (run?.status === 'completed' && !notifiedCompletion) {
			notifiedCompletion = true;
			notifySetupCompleted();
			untrack(() => void load(true));
		}
	});

	$effect(() => {
		if (isAdmin && runChecked && !recipesAttempted) untrack(() => void loadRecipes());
	});

	async function load(isRefresh = false) {
		if (isRefresh) refreshing = true;
		loadError = '';
		try {
			report = await api.getReadiness();
		} catch (err: any) {
			loadError = getApiErrorMessage(err, 'Could not load readiness.');
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	async function loadMachine() {
		try {
			machine = await api.getSetupMachine();
		} catch {
			machine = null;
		} finally {
			machineLoading = false;
		}
	}

	async function loadRecipes() {
		recipesAttempted = true;
		recipesLoading = true;
		recipesError = '';
		try {
			recipes = (await api.getSetupRecipes()).recipes;
		} catch (err: any) {
			recipesError = getApiErrorMessage(err, "Couldn't load the starter models.");
		} finally {
			recipesLoading = false;
		}
	}

	function storedRunId(): string | null {
		if (!browser) return null;
		try {
			return localStorage.getItem(RUN_STORAGE_KEY);
		} catch {
			return null;
		}
	}

	function rememberRunId(runId: string) {
		if (!browser) return;
		try {
			localStorage.setItem(RUN_STORAGE_KEY, runId);
		} catch {
			return;
		}
	}

	function forgetRunId() {
		if (!browser) return;
		try {
			localStorage.removeItem(RUN_STORAGE_KEY);
		} catch {
			return;
		}
	}

	async function discoverRun() {
		const cachedId = storedRunId();
		let cachedRun: SetupRun | null = null;
		if (cachedId) {
			try {
				cachedRun = await api.getSetupRun(cachedId);
				session.adopt(cachedRun);
			} catch {
				cachedRun = null;
			}
		}

		try {
			session.adopt(await api.getSetupActiveRun());
		} catch (err: any) {
			if (err?.response?.status === 404) {
				const decision = decideRunDiscovery('not_found', cachedRun);
				if ((decision.show === 'stored-failed' || decision.show === 'stored-completed') && cachedRun) {
					session.adopt(cachedRun);
				} else {
					const finished = cachedId ? null : await findFinishedOnboardingRun();
					if (finished) {
						session.adopt(finished);
					} else {
						session.reset();
					}
				}
			} else if (!run) {
				runFetchError = "Couldn't check for an in-progress setup — trying again shortly.";
			}
		} finally {
			runChecked = true;
		}
	}

	async function findFinishedOnboardingRun(): Promise<SetupRun | null> {
		try {
			return newestFinishedOnboardingRun((await api.listRecipeRuns({ limit: 20 })).runs);
		} catch {
			return null;
		}
	}

	async function startRecipe(selected: SetupRecipe, options?: { safeInput?: Record<string, unknown> }) {
		if (starting) return;
		starting = true;
		startError = '';
		try {
			session.adopt(await api.createSetupRun(selected.id, options));
			override = null;
		} catch (err: any) {
			startError = getApiErrorMessage(err, "Couldn't start this starter.");
		} finally {
			starting = false;
		}
	}

	function continueFromComputer() {
		writeGuideFlag(COMPUTER_SEEN_KEY, true);
		computerSeen = true;
		override = null;
	}

	function reloadMachine() {
		void loadMachine();
	}

	function goToFirstGeneration() {
		if (generationHandoff) {
			tabsStore.addTabWithData(recipe ? recipe.name : 'First generation', {
				selectedPreset: generationHandoff.presetId,
				selectedMode: generationHandoff.mode
			});
		}
		goto('/generate');
	}

	function skipGuide() {
		const mobile = browser && window.matchMedia('(max-width: 767px)').matches;
		toasts.info(
			skipToastMessage({ mobile, step, runStatus: run?.status ?? null }),
			SKIP_TOAST_DURATION_MS
		);
		goto('/generate');
	}

	onMount(() => {
		computerSeen = readGuideFlag(COMPUTER_SEEN_KEY);
		flagsRead = true;
		load();
		if (isAdmin) {
			discoverRun();
			loadMachine();
		} else {
			runChecked = true;
			machineLoading = false;
		}
	});

	onDestroy(() => session.dispose());
</script>

<svelte:head>
	<title>Setup - PotionUI</title>
</svelte:head>

{#snippet readinessList(current: ReadinessReport)}
	<div class="space-y-3">
		{#each current.checks as check (check.area)}
			{@const adminLink = readinessAdminLink(check.area)}
			<Card>
				<div class="flex items-start justify-between gap-3">
					<div class="min-w-0">
						<div class="mb-1 flex items-center gap-2">
							<h2 class="text-sm font-semibold text-fg">{readinessAreaLabel(check.area)}</h2>
							<Badge variant={readinessCheckBadgeVariant(check)}>
								{readinessCheckStatusLabel(check)}
							</Badge>
						</div>
						<p class="text-sm text-fg-muted">{check.message}</p>
					</div>
				</div>
				{#if isAdmin && check.action}
					<div class="mt-3 flex items-center justify-between gap-3 border-t border-line pt-3">
						<p class="text-xs text-fg-subtle">{check.action}</p>
						{#if adminLink}
							<Button size="sm" variant="secondary" href={adminLink}>Open</Button>
						{/if}
					</div>
				{/if}
			</Card>
		{/each}
	</div>
{/snippet}

<div use:resetScrollOnChange={step} class="flex min-h-screen flex-col bg-canvas text-fg">
	<PageHeader title="Setup" description="Is this instance ready to generate?" sticky={false}>
		{#snippet actions()}
			{#if guideReady && showsSkipGuide(step)}
				<Button variant="secondary" ariaLabel="Skip the guide, go to the app" onclick={skipGuide}>
					<span class="max-sm:hidden">Skip the guide, go to the app</span>
					<span class="sm:hidden">Skip guide</span>
				</Button>
			{/if}
			{#if !isAdmin}
				<Button variant="secondary" icon="refresh" loading={refreshing} onclick={() => load(true)}>
					Refresh
				</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if isAdmin}
		{#if guideReady}
			<SetupStepper
				current={step}
				ownerExists={true}
				onnavigate={(id) => (override = id)}
			/>

			<main
				class="mx-auto flex w-full max-w-[720px] flex-1 flex-col gap-5 px-6 pb-12 pt-8 max-sm:gap-4 max-sm:px-4 max-sm:pb-10 max-sm:pt-5"
			>
				{#if step === 'computer'}
					<ComputerStep
						{machine}
						loading={machineLoading}
						recipes={recipes ?? []}
						onContinue={continueFromComputer}
					/>
				{:else if step === 'starter'}
					<StarterStep
						{recipes}
						loading={recipesLoading}
						error={recipesError}
						{machine}
						{modelsPath}
						{starting}
						{startError}
						onStart={startRecipe}
						onSkip={() => (override = 'ready')}
						onBack={() => (override = 'computer')}
						onRetry={loadRecipes}
						onModelsChanged={reloadMachine}
					/>
				{:else if step === 'install' && run}
					<InstallStep
						{run}
						{recipe}
						{machine}
						fetchError={runFetchError}
						{modelsPath}
						onRunUpdated={session.adopt}
						onStartOver={() => session.reset()}
					/>
				{:else if step === 'ready'}
					<ReadyStep
						{installedName}
						thumbnailUrl={smokeThumbnailUrl}
						{modelsPath}
						{smokeFailure}
						{belowCard}
						{noCard}
						onCreate={goToFirstGeneration}
					/>
				{/if}

				{#if report || loadError}
					<details class="pt-2">
						<summary class="cursor-pointer list-none rounded-sm font-medium text-signal hover:underline">
							Readiness checks
						</summary>
						<div class="mt-3 space-y-3">
							<div class="flex items-center justify-between gap-3">
								<p class="text-sm text-fg-muted">
									{report ? readinessHeadline(report) : loadError}
								</p>
								<Button size="sm" variant="secondary" icon="refresh" loading={refreshing} onclick={() => load(true)}>
									Refresh
								</Button>
							</div>
							{#if report}
								{@render readinessList(report)}
							{/if}
						</div>
					</details>
				{/if}
			</main>

			<TrustFooter />
		{:else}
			<div class="flex flex-1 justify-center py-16">
				<Spinner size="lg" />
			</div>
		{/if}
	{:else}
		<main class="mx-auto w-full max-w-2xl space-y-6 px-4 py-4 sm:px-6 sm:py-6">
			{#if loading}
				<div class="flex justify-center py-16">
					<Spinner size="lg" />
				</div>
			{:else if loadError}
				<EmptyState title="Couldn't load readiness" description={loadError} icon="warning">
					{#snippet actions()}
						<Button variant="secondary" onclick={() => load()}>Try again</Button>
					{/snippet}
				</EmptyState>
			{:else if report}
				<h1 class="text-xl font-semibold text-fg">{readinessHeadline(report)}</h1>
				{#if report.overall === 'ready'}
					<Card padding="none" class="px-6 py-10 text-center">
						<p class="mb-4 text-sm text-fg-muted">
							This instance is generating successfully. You're all set.
						</p>
						<Button variant="primary" href="/generate">Go to Generate</Button>
					</Card>
				{:else}
					{@render readinessList(report)}
				{/if}
			{/if}
		</main>
	{/if}
</div>
