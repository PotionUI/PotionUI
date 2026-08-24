<!--
	Camera-shot phrasebook: pick how to describe a shot (angle / distance /
	orientation / motion) for the model this preset targets, then insert or copy the
	phrase into the prompt. Two equivalent ways to drive the SAME selection: a tile
	grid (default) and a 3D orbit viewfinder. Phrasing ships with the preset (field
	`vocabulary`, resolved server-side) — no model lookup. Stores NO form value; it
	is a helper surface (see camera_shot.py `input` returning None).
-->
<script lang="ts">
	import { toasts } from '$lib/stores/toast';
	import { insertTriggerIntoActivePrompt } from '$lib/utils/promptInsertion';
	import { copyText } from '$lib/utils/clipboard';
	import {
		applyPoseToSelection,
		composedPhrase,
		findShotByKey,
		poseSelection,
		toggleShot,
		type CameraCategory,
		type CameraPose
	} from '$lib/utils/cameraShot';
	import CameraOrbit from './CameraOrbit.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';

	// Standard field props. This field is display-only, so `value`/`onChange` are
	// intentionally unused.
	export let name: string | null = null;
	export let config: any = {};
	export let value: any = undefined;
	export let onChange: (fieldName: string, value: any) => void = () => {};
	void name;
	void value;
	void onChange;

	$: label = config.title || config.name || 'Camera & Shot';
	$: description = config.description || '';
	// Fully resolved catalog embedded by the backend (phrase + overridden per shot).
	$: catalog = (Array.isArray(config.catalog) ? config.catalog : []) as CameraCategory[];

	// Shared selection: at most one shot per slot (dutch stands apart from angle).
	let selection: string[] = [];
	let view: 'grid' | 'orbit' = 'grid';
	let justInserted = false;
	let pose: CameraPose = { azimuth: 0, elevation: 0, distance: 4, roll: 0 };

	$: phrase = composedPhrase(catalog, selection);
	$: orbitCaption = poseSelection(pose, catalog)
		.map((key) => findShotByKey(catalog, key)?.label)
		.filter(Boolean)
		.join(' · ');

	function toggle(key: string) {
		selection = toggleShot(selection, key, catalog);
		justInserted = false;
	}

	function handlePose(next: CameraPose) {
		pose = next;
		selection = applyPoseToSelection(selection, pose, catalog);
		justInserted = false;
	}

	function clearSelection() {
		selection = [];
		justInserted = false;
	}

	function insertPhrase() {
		if (!phrase) return;
		const result = insertTriggerIntoActivePrompt(phrase);
		if (result === 'inserted') {
			justInserted = true;
			toasts.info('Inserted into prompt');
		} else if (result === 'duplicate') {
			toasts.info('Already in prompt');
		} else {
			void copyText(phrase);
			toasts.info('Copied to clipboard');
		}
	}

	async function copyPhrase() {
		if (!phrase) return;
		const ok = await copyText(phrase);
		toasts.info(ok ? 'Copied to clipboard' : 'Could not copy');
	}
</script>

<div class="rounded-lg border border-line-strong bg-surface-1 p-3">
	<div class="flex items-center justify-between gap-2 mb-1">
		<div class="flex items-center gap-2">
			<Icon name="film" className="w-4 h-4 text-fg-subtle" />
			<label class="label !mb-0" for={undefined}>{label}</label>
		</div>
		<div class="inline-flex items-center rounded border border-line overflow-hidden text-xs">
			<button
				type="button"
				class="px-2 py-0.5 transition-colors {view === 'grid'
					? 'bg-signal/10 text-signal'
					: 'text-fg-muted hover:text-fg'}"
				aria-pressed={view === 'grid'}
				on:click={() => (view = 'grid')}
			>
				Grid
			</button>
			<button
				type="button"
				class="px-2 py-0.5 border-l border-line transition-colors {view === 'orbit'
					? 'bg-signal/10 text-signal'
					: 'text-fg-muted hover:text-fg'}"
				aria-pressed={view === 'orbit'}
				on:click={() => (view = 'orbit')}
			>
				3D
			</button>
		</div>
	</div>

	{#if description}
		<p class="text-xs text-fg-muted mb-2">{description}</p>
	{/if}

	{#if view === 'grid'}
		<div class="flex flex-col gap-3 mt-1">
			{#each catalog as category (category.key)}
				<div>
					<div class="text-2xs uppercase tracking-wide text-fg-subtle mb-1.5">{category.label}</div>
					<div class="flex flex-wrap gap-1.5">
						{#each category.shots as shot (shot.key)}
							<button
								type="button"
								class="inline-flex items-center gap-1 px-2 py-1 text-xs rounded border transition-colors {selection.includes(
									shot.key
								)
									? 'bg-signal/10 text-signal border-signal/40'
									: 'bg-surface-2 text-fg-muted border-line hover:text-fg hover:border-line-hover'}"
								title={shot.phrase}
								aria-pressed={selection.includes(shot.key)}
								on:click={() => toggle(shot.key)}
							>
								{shot.label}
							</button>
						{/each}
					</div>
				</div>
			{/each}
		</div>
	{:else}
		<div class="mt-1">
			<CameraOrbit {pose} onPose={handlePose} caption={orbitCaption} />
		</div>
	{/if}

	{#if phrase}
		<div class="mt-3 bg-surface-2 border border-line rounded-lg p-3">
			<div class="flex items-start justify-between gap-2">
				<div class="min-w-0">
					<div class="text-2xs uppercase tracking-wide text-fg-subtle mb-1">Phrase</div>
					<p class="text-sm text-fg leading-relaxed break-words">{phrase}</p>
				</div>
				<button
					class="shrink-0 text-fg-subtle hover:text-fg-muted p-1 -m-1"
					aria-label="Clear selection"
					on:click={clearSelection}
				>
					<Icon name="close" className="w-4 h-4" />
				</button>
			</div>
			<div class="flex gap-2 justify-end mt-3">
				<Button size="xs" variant="secondary" icon="copy" onclick={copyPhrase}>Copy</Button>
				<Button size="xs" variant="primary" icon={justInserted ? 'check' : 'plus'} onclick={insertPhrase}>
					Insert at cursor
				</Button>
			</div>
		</div>
	{/if}
</div>
