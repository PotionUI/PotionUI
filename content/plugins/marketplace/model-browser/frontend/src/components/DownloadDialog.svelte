<script>
	import { onMount } from 'svelte';
	import HostUi from '../../../../../sdk/HostUi.svelte';
	import Icon from './Icon.svelte';
	import Tip from './Tip.svelte';
	import LicenceBox from './LicenceBox.svelte';
	import { listRemoteBackends, startDownload } from '../lib/client.js';
	import { allowedTypes, canChooseGroup, canChooseRemote, groupChoices } from '../lib/capabilities.js';
	import {
		baseName,
		buildDownloadBody,
		canSubmit,
		downloadWarnings,
		extensionOf,
		formProblems,
		initialForm,
		suggestedModelType
	} from '../lib/downloadForm.js';
	import { formatBytes, modelTypeLabel } from '../lib/format.js';
	import { startToast } from '../lib/downloads.js';
	import { escapeCloses, hostLayer, portal, toast, trapFocus } from '../lib/dom.js';

	let { api, me, target, onClose, onStarted } = $props();

	const uid = $props.id();
	let mounted = $state(false);
	const startingForm = () => initialForm(me, target.detail, target.file);
	let form = $state(startingForm());
	let busy = $state(false);
	let serverError = $state.raw(null);
	let remotes = $state.raw([]);
	let remoteState = $state('idle');
	let remoteError = $state('');

	const detail = $derived(target.detail);
	const version = $derived(target.version);
	const file = $derived(target.file);
	const suggestion = $derived(suggestedModelType(detail, file));
	const typeChoice = $derived(allowedTypes(me, suggestion));
	const problems = $derived(formProblems(form, { me, detail, file }));
	const ready = $derived(canSubmit(problems) && !busy);
	const warnings = $derived(downloadWarnings({ detail, version, file }));
	const showRemote = $derived(canChooseRemote(me));
	const showGroups = $derived(canChooseGroup(me) && (me.groups?.length ?? 0) > 0);
	const groupNote = $derived(!me.params?.unrestricted && me.params?.assign_to === 'group' && (me.groups?.length ?? 0) === 0);
	const extension = $derived(extensionOf(file.name));
	const sizeText = $derived(formatBytes(file.size_bytes));
	const typeError = $derived(form.modelType && problems.modelType ? problems.modelType : null);
	const typeHelp = $derived(
		suggestion
			? typeChoice.suggestionAllowed
				? `Suggested by the source: ${modelTypeLabel(suggestion)}`
				: `The source suggests ${modelTypeLabel(suggestion)}, which your permissions do not allow: choose another type`
			: `The source's type (${detail.type || 'unknown'}) has no PotionUI equivalent: choose one if you know what this file is`
	);
	const destinationHelp = $derived(
		remoteState === 'error'
			? `Couldn't list the remote backends: ${remoteError}`
			: remoteState === 'ready' && remotes.length === 0
				? 'No remote backend is enabled: the file goes to this server'
				: 'Where the file is stored'
	);

	onMount(() => {
		mounted = true;
		if (showRemote) loadRemotes();
	});

	async function loadRemotes() {
		remoteState = 'loading';
		const result = await listRemoteBackends();
		if (result.ok) {
			remotes = result.data;
			remoteState = 'ready';
		} else {
			remoteError = result.error.message;
			remoteState = 'error';
		}
	}

	async function submit(event) {
		event?.preventDefault?.();
		if (!ready) return;
		busy = true;
		serverError = null;
		const result = await startDownload(api, buildDownloadBody({ detail, version, file, form }));
		busy = false;
		if (!result.ok) {
			serverError = result.error;
			toast('error', `Download refused: ${result.error.message}`);
			return;
		}
		const message = startToast(result.data, me.groups);
		toast(message.level, message.message);
		onStarted?.(result.data);
		onClose?.();
	}

	function onBackdrop(event) {
		if (event.target === event.currentTarget && !busy) onClose?.();
	}
</script>

{#if mounted}
<div
	use:portal
	use:hostLayer
	use:escapeCloses={() => {
		if (!busy) onClose?.();
	}}
	class="fixed inset-0 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
	role="presentation"
	onclick={onBackdrop}
>
	<div
		class="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl bg-surface-1 shadow-overlay outline-none"
		role="dialog"
		aria-modal="true"
		aria-labelledby="{uid}-title"
		tabindex="-1"
		use:trapFocus={target.opener}
		onkeydown={(event) => event.stopPropagation()}
	>
	<form class="flex min-h-0 flex-1 flex-col" novalidate onsubmit={submit}>
		<header class="flex items-start gap-3 border-b border-line px-5 py-4">
			<div class="min-w-0 flex-1">
				<Tip text={file.name} wrapperClass="min-w-0 flex">
					<h2 id="{uid}-title" class="truncate text-lg font-semibold text-fg">Download {baseName(file.name)}</h2>
				</Tip>
				<p class="mt-0.5 truncate text-xs text-fg-muted">
					{[detail.name, version.name, sizeText].filter(Boolean).join(' · ')}
				</p>
			</div>
			<button
				type="button"
				class="inline-flex min-h-8 min-w-8 flex-shrink-0 items-center justify-center rounded p-1.5 text-fg-muted transition-colors duration-100 hover:bg-surface-3/50 hover:text-fg"
				aria-label="Close"
				disabled={busy}
				onclick={() => onClose?.()}
			>
				<Icon name="close" class="h-4 w-4" />
			</button>
		</header>

		<div class="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
			{#each warnings as warning (warning.title)}
				<HostUi name="Alert" props={{ variant: warning.tone, density: 'compact', icon: true, title: warning.title }}>
					{warning.text}
				</HostUi>
			{/each}

			{#if problems.size}
				<HostUi name="Alert" props={{ variant: 'danger', density: 'compact', icon: true, title: 'Too large for your permissions' }}>
					{problems.size}
				</HostUi>
			{/if}

			<HostUi name="DetailField" props={{ label: 'File as', id: `${uid}-type`, wide: true, error: typeError, help: typeHelp }}>
				<select id="{uid}-type" class="input w-full text-sm" bind:value={form.modelType} disabled={busy} data-autofocus>
					{#if !form.modelType}
						<option value="" disabled>Choose a model type…</option>
					{/if}
					{#each typeChoice.types as type (type)}
						<option value={type}>{modelTypeLabel(type)}</option>
					{/each}
				</select>
			</HostUi>

			<HostUi name="DetailField" props={{ label: 'Subfolder (optional)', id: `${uid}-subdir`, wide: true, error: problems.subdir || null, help: 'Inside the folder of the model type, e.g. styles/anime' }}>
				<input
					id="{uid}-subdir"
					type="text"
					class="input w-full font-mono text-sm {problems.subdir ? 'border-danger' : ''}"
					placeholder="(none)"
					autocomplete="off"
					spellcheck="false"
					bind:value={form.subdir}
					disabled={busy}
					aria-invalid={problems.subdir ? 'true' : undefined}
				/>
			</HostUi>

			<HostUi name="DetailField" props={{ label: 'File name', id: `${uid}-filename`, wide: true, error: problems.filename || null, help: extension ? `Keep the ${extension} extension; leave it as it is to keep the source's name` : "Leave it as it is to keep the source's name" }}>
				<input
					id="{uid}-filename"
					type="text"
					class="input w-full font-mono text-sm {problems.filename ? 'border-danger' : ''}"
					autocomplete="off"
					spellcheck="false"
					bind:value={form.filename}
					disabled={busy}
					aria-invalid={problems.filename ? 'true' : undefined}
				/>
			</HostUi>

			{#if showRemote}
				<HostUi name="DetailField" props={{ label: 'Destination', id: `${uid}-destination`, wide: true, help: destinationHelp }}>
					<select id="{uid}-destination" class="input w-full text-sm" bind:value={form.destination} disabled={busy || remoteState === 'loading'}>
						<option value="">This server</option>
						{#each remotes as remote (remote.id)}
							<option value={remote.id}>{remote.name} (remote)</option>
						{/each}
					</select>
				</HostUi>
			{/if}

			{#if showGroups}
				<HostUi name="DetailField" props={{ label: 'Assign to', id: `${uid}-group`, wide: true, help: 'Who sees the model once it is downloaded' }}>
					<select id="{uid}-group" class="input w-full text-sm" bind:value={form.group} disabled={busy}>
						{#each groupChoices(me) as choice (choice.id)}
							<option value={choice.id}>{choice.id ? `Group: ${choice.name}` : choice.name}</option>
						{/each}
					</select>
				</HostUi>
			{:else if groupNote}
				<p class="text-xs text-fg-subtle">You are in no user group: the model is assigned to you.</p>
			{/if}

			<LicenceBox license={detail.license} />

			{#if serverError}
				<HostUi name="Alert" props={{ variant: 'danger', density: 'compact', icon: true, title: 'The server refused this download' }}>
					{serverError.message}
				</HostUi>
			{/if}
		</div>

		<footer class="flex items-center justify-end gap-2 border-t border-line px-5 py-3">
			<HostUi name="Button" props={{ variant: 'ghost', size: 'sm', disabled: busy, onclick: () => onClose?.() }}>Cancel</HostUi>
			<HostUi name="Button" props={{ variant: 'primary', size: 'sm', type: 'submit', icon: 'download', loading: busy, disabled: !ready }}>Download</HostUi>
		</footer>
	</form>
	</div>
</div>
{/if}
