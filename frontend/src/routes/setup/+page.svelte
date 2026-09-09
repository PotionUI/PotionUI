<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { tabsStore } from '$lib/stores/tabs';
	import { api } from '$lib/services/api/index';
	import type { ReadinessReport, SetupRecipe, SetupRun } from '$lib/services/api/setup';
	import {
		readinessBadgeVariant,
		readinessAreaLabel,
		readinessAdminLink,
		readinessHeadline
	} from '$lib/utils/readinessDisplay';
	import {
		shouldPollRun,
		decideRunDiscovery,
		extractGenerationHandoff,
		extractSmokeGeneration,
		RUN_POLL_INTERVAL_MS
	} from '$lib/utils/setupRunDisplay';
	import { formatBytes } from '$lib/utils/format';
	import { notifySetupCompleted } from '$lib/stores/setupCompletion';
	import { Badge, Button, Card, EmptyState, PageContainer, PageHeader, Spinner } from '$lib/components/ui';
	import RecipeRunProgress from '$lib/components/recipes/RecipeRunProgress.svelte';
	import { setupRunActions } from '$lib/components/recipes/runActions';
	import ModelsLocationStep from './components/ModelsLocationStep.svelte';

	$: isAdmin = $authStore.user?.account_type === 'ADMIN';

	let report: ReadinessReport | null = null;
	let loading = true;
	let refreshing = false;
	let loadError = '';

	async function load(isRefresh = false) {
		if (isRefresh) refreshing = true;
		loadError = '';
		try {
			report = await api.getReadiness();
		} catch (err: any) {
			loadError = err?.response?.data?.detail?.message || err?.message || 'Could not load readiness.';
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	// --- guided setup run --------------------------------------------------
	//
	// The server is the source of truth for "is there an active run" — this
	// page always calls `GET /api/setup/runs/active` on load, so a run
	// started in another browser/session still shows up here. The last-seen
	// run id is still kept in localStorage, but only as an optimistic
	// fast-path (paint whatever was last seen immediately, while the real
	// check is in flight) — a 404 from the server always wins and clears it.
	const RUN_STORAGE_KEY = 'potionui:setup:activeRunId';

	let run: SetupRun | null = null;
	let runChecked = false;
	let runFetchError = '';
	let pollTimer: ReturnType<typeof setTimeout> | null = null;

	// Pokes the sidebar's "Resume setup" nudge to re-check the instant this
	// run finishes, instead of it lingering until the next full page load —
	// once per page lifetime is enough (the sidebar itself de-dupes further).
	let notifiedCompletion = false;
	$: if (run?.status === 'completed' && !notifiedCompletion) {
		notifiedCompletion = true;
		notifySetupCompleted();
	}
	$: generationHandoff = run ? extractGenerationHandoff(run) : null;
	$: smokeResult = run ? extractSmokeGeneration(run) : null;
	$: smokeThumbnailUrl =
		smokeResult?.filename && smokeResult.generationId
			? api.getGenerationThumbnailURL(smokeResult.generationId, smokeResult.filename, 'medium')
			: null;

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
			// localStorage may be unavailable — the panel still works for this
			// page load, it just won't survive a refresh.
		}
	}

	function forgetRunId() {
		if (!browser) return;
		try {
			localStorage.removeItem(RUN_STORAGE_KEY);
		} catch {
			// ignore
		}
	}

	function clearPoll() {
		if (pollTimer) {
			clearTimeout(pollTimer);
			pollTimer = null;
		}
	}

	function schedulePoll(runId: string) {
		clearPoll();
		pollTimer = setTimeout(() => refreshRun(runId), RUN_POLL_INTERVAL_MS);
	}

	async function refreshRun(runId: string) {
		try {
			const fetched = await api.getSetupRun(runId);
			run = fetched;
			runFetchError = '';
			rememberRunId(fetched.id);
			if (shouldPollRun(fetched.status)) schedulePoll(fetched.id);
		} catch (err: any) {
			// Fail soft: a stale/deleted run id means give up quietly; any other
			// fetch error (network blip, timeout) keeps the last-known view on
			// screen with a small retry notice, and keeps trying.
			if (err?.response?.status === 404) {
				forgetRunId();
				run = null;
				return;
			}
			runFetchError = "Couldn't check on setup progress — trying again shortly.";
			schedulePoll(runId);
		}
	}

	async function discoverRun() {
		const cachedId = storedRunId();
		let cachedRun: SetupRun | null = null;
		if (cachedId) {
			// Optimistic fast path: paint whatever this browser last saw for
			// this run id immediately, while the authoritative check below is
			// still in flight. Best-effort only — any failure here is ignored,
			// the authoritative call settles the real state.
			try {
				cachedRun = await api.getSetupRun(cachedId);
				run = cachedRun;
			} catch {
				// ignore — a 404/network error here just means there's nothing to
				// fall back on below.
			}
		}

		try {
			const active = await api.getSetupActiveRun();
			run = active;
			runFetchError = '';
			rememberRunId(active.id);
			if (shouldPollRun(active.status)) schedulePoll(active.id);
		} catch (err: any) {
			if (err?.response?.status === 404) {
				// Authoritative: nothing is active right now. A failed stored run
				// still gets one more look (it's deliberately excluded from
				// "active" — see decideRunDiscovery) so "Try again" survives a
				// reload; anything else (completed/cancelled/no stored run) clears.
				const decision = decideRunDiscovery('not_found', cachedRun);
				if (decision.show === 'stored-failed' && cachedRun) {
					run = cachedRun;
				} else {
					forgetRunId();
					run = null;
				}
			} else if (!run) {
				runFetchError = "Couldn't check for an in-progress setup — trying again shortly.";
			}
		} finally {
			runChecked = true;
		}
	}

	/** Quiet escape hatch from a failed run that's no longer going anywhere on
	 * its own — clears the stored id and drops back to the recipe catalog. */
	function startOver() {
		clearPoll();
		forgetRunId();
		run = null;
	}

	/** A run handed back by one of `RecipeRunProgress`'s actions (approve,
	 * cancel, retry, and retry's optimistic pre-flip) — adopt it and keep the
	 * poll in step with whatever status it now carries. */
	function handleRunUpdated(updated: SetupRun) {
		run = updated;
		if (shouldPollRun(updated.status)) schedulePoll(updated.id);
		else clearPoll();
	}

	// --- start-a-recipe -----------------------------------------------------

	let recipes: SetupRecipe[] | null = null;
	let recipesLoading = false;
	let recipesError = '';
	let startingRecipeId: string | null = null;
	let startError = '';

	async function loadRecipes() {
		recipesLoading = true;
		recipesError = '';
		try {
			const result = await api.getSetupRecipes();
			recipes = result.recipes;
		} catch (err: any) {
			recipesError =
				err?.response?.data?.detail?.message ||
				err?.response?.data?.detail ||
				err?.message ||
				"Couldn't load the setup recipes.";
		} finally {
			recipesLoading = false;
		}
	}

	// Load the catalog once we know there's no active run to show instead.
	$: if (isAdmin && runChecked && !run && recipes === null && !recipesLoading) loadRecipes();

	async function startRecipe(recipe: SetupRecipe) {
		if (startingRecipeId) return;
		startingRecipeId = recipe.id;
		startError = '';
		try {
			const created = await api.createSetupRun(recipe.id);
			run = created;
			rememberRunId(created.id);
			if (shouldPollRun(created.status)) schedulePoll(created.id);
		} catch (err: any) {
			startError = err?.response?.data?.detail || err?.message || "Couldn't start this recipe.";
		} finally {
			startingRecipeId = null;
		}
	}

	// --- first-generation handoff --------------------------------------

	function goToFirstGeneration() {
		if (generationHandoff) {
			const recipe = recipes?.find((r) => r.id === run?.recipe_id);
			const tabName = recipe ? recipe.name : 'First generation';
			tabsStore.addTabWithData(tabName, {
				selectedPreset: generationHandoff.presetId,
				selectedMode: generationHandoff.mode
			});
		}
		goto('/generate');
	}

	onMount(() => {
		load();
		if (isAdmin) discoverRun();
		else runChecked = true;
	});

	onDestroy(() => clearPoll());
</script>

<svelte:head>
	<title>Setup - PotionUI</title>
</svelte:head>

<div class="min-h-screen bg-canvas text-fg">
	<PageHeader title="Setup" description="Is this instance ready to generate?" sticky={false}>
		{#snippet actions()}
			<Button variant="secondary" icon="refresh" loading={refreshing} onclick={() => load(true)}>
				Refresh
			</Button>
		{/snippet}
	</PageHeader>

	<PageContainer width="sm" class="space-y-6">
		{#if loading}
			<div class="flex justify-center py-16">
				<Spinner size="lg" />
			</div>
		{:else if loadError}
			<EmptyState
				title="Couldn't load readiness"
				description={loadError}
				icon="warning"
			>
				{#snippet actions()}
					<Button variant="secondary" onclick={() => load()}>Try again</Button>
				{/snippet}
			</EmptyState>
		{:else if report}
			<div>
				<h1 class="text-xl font-semibold text-fg">{readinessHeadline(report)}</h1>
			</div>

			{#if isAdmin && runChecked && run}
				<!-- A snippet is hoisted out of this block, so it neither inherits the
				     null-narrowing above nor may be declared inside <Card> (that would
				     make it a Card prop). Both are settled by naming the run here. -->
				{@const currentRun = run}
				{#snippet completedHandoff()}
					{#if currentRun.status === 'completed'}
						<!-- first-generation handoff -->
						<div class="rounded-lg border border-success/25 bg-success/5 px-4 py-5 text-center">
							<p class="text-sm font-semibold text-success">You're all set</p>
							<p class="text-sm text-fg-muted mt-1">
								This instance is ready to generate. Your recipe installed everything it needed.
							</p>

							{#if smokeThumbnailUrl}
								<div class="mt-4 flex flex-col items-center gap-2">
									<img
										src={smokeThumbnailUrl}
										alt="Output from your setup's test generation"
										class="max-h-56 rounded-lg border border-line shadow-raised"
									/>
									<p class="text-xs text-fg-subtle">Here's the test image your setup produced.</p>
								</div>
							{/if}

							<div class="mt-4">
								<Button variant="primary" icon="sparkles" onclick={goToFirstGeneration}>
									Create your first image
								</Button>
							</div>
						</div>
					{/if}
				{/snippet}
				<Card>
					<RecipeRunProgress
						run={currentRun}
						fetchError={runFetchError}
						actions={setupRunActions}
						onRunUpdated={handleRunUpdated}
						onStartOver={startOver}
						completed={completedHandoff}
					/>
				</Card>
			{:else if isAdmin && runChecked && !run}
				<!-- Models location: before offering recipes (which download models),
				     let the admin point at an external directory. Skippable. -->
				<ModelsLocationStep />

				<!-- Start-a-recipe: no active run, offer the recipe catalog. -->
				<div class="space-y-3">
					<h2 class="text-sm font-semibold text-fg">Start guided setup</h2>

					{#if recipesLoading}
						<div class="flex justify-center py-10">
							<Spinner size="md" />
						</div>
					{:else if recipesError}
						<EmptyState title="Couldn't load setup recipes" description={recipesError} icon="warning">
							{#snippet actions()}
								<Button variant="secondary" onclick={loadRecipes}>Try again</Button>
							{/snippet}
						</EmptyState>
					{:else if recipes && recipes.length === 0}
						<EmptyState
							title="No setup recipes available"
							description="There's nothing to guide you through right now — check back after this instance is updated."
							icon="box"
						/>
					{:else if recipes}
						{#if startError}
							<p class="text-sm text-danger">{startError}</p>
						{/if}
						<div class="space-y-3">
							{#each recipes as recipe (recipe.id)}
								<Card class="space-y-2">
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0">
											<h3 class="text-sm font-semibold text-fg">{recipe.name}</h3>
											<p class="text-sm text-fg-muted mt-0.5">{recipe.summary}</p>
											{#if recipe.description}
												<p class="text-xs text-fg-subtle mt-1 line-clamp-3">{recipe.description}</p>
											{/if}
										</div>
										<Button
											size="sm"
											variant={recipe.last_completed_at ? 'secondary' : 'primary'}
											loading={startingRecipeId === recipe.id}
											disabled={startingRecipeId !== null && startingRecipeId !== recipe.id}
											onclick={() => startRecipe(recipe)}
										>
											{recipe.last_completed_at ? 'Run again' : 'Start'}
										</Button>
									</div>
									<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-subtle">
										{#if recipe.last_completed_at}
											<Badge size="sm" variant="success">Installed</Badge>
										{/if}
										<span class="font-mono">{recipe.engine}</span>
										{#if recipe.preset_name}
											<span>{recipe.preset_name}</span>
										{/if}
										{#if recipe.total_download_bytes != null}
											<span class="font-mono tabular-nums">
												~{formatBytes(recipe.total_download_bytes)} to download
											</span>
										{/if}
									</div>
								</Card>
							{/each}
						</div>
					{/if}
				</div>
			{/if}

			{#if report.overall === 'ready'}
				<Card padding="none" class="text-center py-10 px-6">
					<p class="text-sm text-fg-muted mb-4">
						This instance is generating successfully. You're all set.
					</p>
					<Button variant="primary" href="/generate">Go to Generate</Button>
				</Card>
			{:else}
				<div class="space-y-3">
					{#each report.checks as check (check.area)}
						{@const adminLink = readinessAdminLink(check.area)}
						<Card>
							<div class="flex items-start justify-between gap-3">
								<div class="min-w-0">
									<div class="flex items-center gap-2 mb-1">
										<h2 class="text-sm font-semibold text-fg">{readinessAreaLabel(check.area)}</h2>
										<Badge variant={readinessBadgeVariant(check.status)}>
											{check.status.replace('_', ' ')}
										</Badge>
									</div>
									<p class="text-sm text-fg-muted">{check.message}</p>
								</div>
							</div>
							{#if isAdmin && check.action}
								<div class="mt-3 pt-3 border-t border-line flex items-center justify-between gap-3">
									<p class="text-xs text-fg-subtle">{check.action}</p>
									{#if adminLink}
										<Button size="sm" variant="secondary" href={adminLink}>Open</Button>
									{/if}
								</div>
							{/if}
						</Card>
					{/each}
				</div>
			{/if}
		{/if}
	</PageContainer>
</div>
