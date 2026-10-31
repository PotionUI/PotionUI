<script lang="ts">
	import { onDestroy } from 'svelte';
	import { Alert, Button, Spinner } from '$lib/components/ui';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { modeInstallRequest, closeModeInstall, type ModeInstallRequest } from '$lib/stores/modeInstall';
	import { announceModelInstalled } from '$lib/stores/modelInstalls';
	import { recipeCatalog } from '$lib/stores/recipeCatalog';
	import { RecipeRunSession } from '../recipeRunSession.svelte';
	import RecipeRunProgress from '../RecipeRunProgress.svelte';
	import { adminRecipeRunActions } from '../runActions';
	import { api } from '$lib/services/api/index';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { isConsentStatus, isRunTerminal, runStatusLabel } from '$lib/utils/setupRunDisplay';
	import ModeInstallSheet from './ModeInstallSheet.svelte';
	import { preselectOf, replanFor } from './modeInstallActions';
	import { extractModesConsent } from './modeInstallModel';
	import type { ModesGrantBody } from '$lib/services/api/recipeModes';

	const session = new RecipeRunSession({
		onFinished: (run) => {
			if (run.status === 'completed') announceModelInstalled();
			void refreshCatalog();
		}
	});

	let request = $state<ModeInstallRequest | null>(null);
	let grantBusy = $state(false);
	let grantError = $state('');
	let cancelError = $state('');
	let startedId = 0;

	const unsubscribe = modeInstallRequest.subscribe((value) => {
		request = value;
		if (!value) {
			session.reset();
			startedId = 0;
			return;
		}
		if (value.id !== startedId) {
			startedId = value.id;
			grantError = '';
			cancelError = '';
			session.reset();
			void session.start(value.recipeId, value.modes);
		}
	});

	onDestroy(() => {
		unsubscribe();
		session.dispose();
	});

	async function refreshCatalog() {
		try {
			const result = await api.listRecipes();
			recipeCatalog.set(result.recipes ?? []);
		} catch {
			return;
		}
	}

	const run = $derived(session.run);
	const awaitingModes = $derived.by(() => {
		if (!run || !isConsentStatus(run.status)) return null;
		const attempt = run.steps
			?.flatMap((step) => step.attempts ?? [])
			.filter((entry) => entry.status === 'awaiting_consent')
			.at(-1);
		return attempt ? { stepKey: attempt.step_key, consent: extractModesConsent(attempt) } : null;
	});
	const showSheet = $derived(!!request && !!awaitingModes?.consent);

	async function approve(body: ModesGrantBody) {
		if (!run || !awaitingModes || grantBusy) return false;
		grantBusy = true;
		grantError = '';
		try {
			const { selections, ...extra } = body;
			session.adopt(await adminRecipeRunActions.grantConsent(run.id, awaitingModes.stepKey, selections, extra));
			return true;
		} catch (error) {
			grantError = getApiErrorMessage(error, "Couldn't approve the download.");
			return false;
		} finally {
			grantBusy = false;
		}
	}

	const downloadStarted = $derived(
		!!run?.steps?.some((step) => step.kind === 'artifacts.fetch' && step.status !== 'pending')
	);

	async function cancelAndClose() {
		const active = session.run;
		cancelError = '';
		if (active && !isRunTerminal(active.status)) {
			try {
				session.adopt(await adminRecipeRunActions.applyAction(active.id, 'cancel'));
			} catch (error) {
				cancelError = getApiErrorMessage(error, "Couldn't cancel this install.");
				return;
			}
		}
		closeModeInstall();
	}

	function dismiss() {
		const active = session.run;
		if (active && !isRunTerminal(active.status) && !downloadStarted) {
			void cancelAndClose();
			return;
		}
		closeModeInstall();
	}

	function openConflictRun() {
		const conflict = session.conflict;
		if (!conflict) return;
		void api.getRecipeRun(conflict.activeRun.id).then(
			(active) => {
				session.conflict = null;
				session.adopt(active);
			},
			(error) => {
				cancelError = getApiErrorMessage(error, "Couldn't open that install.");
			}
		);
	}
</script>

{#if request}
	{#if showSheet && awaitingModes?.consent && run}
		{#key `${run.id}:${awaitingModes.stepKey}`}
			<ModeInstallSheet
				request={awaitingModes.consent}
				preselect={preselectOf(run)}
				busy={grantBusy}
				error={grantError || cancelError}
				onConfirm={approve}
				onCancel={cancelAndClose}
				onReplan={replanFor(run.recipe_id)}
			/>
		{/key}
	{:else}
		<BaseModal isOpen={true} title="Install modes" size="lg" compact on:close={dismiss}>
			<div class="space-y-3 p-5" data-mode-install-host>
				{#if session.conflict}
					{@const conflictRun = session.conflict.activeRun}
					<Alert variant="warning" density="compact" title="Another recipe is running">
						{conflictRun.recipeName} is still running ({runStatusLabel(conflictRun.status).toLowerCase()}).
						{#snippet actions()}
							<div class="flex items-center gap-2">
								<Button variant="secondary" size="sm" onclick={openConflictRun}>Open it</Button>
								<Button variant="secondary" size="sm" onclick={() => session.cancelConflict()}>Cancel it</Button>
							</div>
						{/snippet}
					</Alert>
				{#if session.error || cancelError}
						<p class="text-sm text-danger" data-mode-install-error>{session.error || cancelError}</p>
					{/if}
				{:else if session.error}
					<Alert variant="danger" density="compact" title="Couldn't start this install">{session.error}</Alert>
				{:else if run}
					{#if cancelError}
						<p class="text-sm text-danger" data-mode-install-error>{cancelError}</p>
					{/if}
					<RecipeRunProgress
						{run}
						title="Installing modes"
						actions={adminRecipeRunActions}
						onRunUpdated={session.adopt}
					/>
				{:else}
					<div class="flex items-center gap-3 py-6 text-sm text-fg-muted">
						<Spinner size="sm" /> Checking your files…
					</div>
				{/if}
			</div>
		</BaseModal>
	{/if}
{/if}
