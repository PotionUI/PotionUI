<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Alert, Badge, Button, Input } from '$lib/components/ui';
	import { confirmDialog } from '$lib/stores/confirm';
	import {
		catalogActionLabel,
		catalogStatusLabel,
		catalogStatusTone,
		installFromCatalog,
		readPluginCatalog,
		rememberCatalogUrl,
		rememberedCatalogUrl,
		type PluginCatalog,
		type PluginCatalogEntry,
		type PluginCatalogInstallResult
	} from './pluginCatalog';

	let {
		isOpen = false,
		onClose,
		onInstalled
	}: {
		isOpen?: boolean;
		onClose: () => void;
		onInstalled: (result: PluginCatalogInstallResult) => void | Promise<void>;
	} = $props();

	let url = $state(rememberedCatalogUrl());
	let catalog = $state<PluginCatalog | null>(null);
	let reading = $state(false);
	let installingId = $state<string | null>(null);
	let error = $state<string | null>(null);
	let installed = $state<PluginCatalogInstallResult | null>(null);

	async function readCatalog() {
		const target = url.trim();
		if (!target || reading) return;
		reading = true;
		error = null;
		try {
			catalog = await readPluginCatalog(target);
			rememberCatalogUrl(target);
		} catch (err) {
			catalog = null;
			error = err instanceof Error ? err.message : String(err);
		} finally {
			reading = false;
		}
	}

	async function install(entry: PluginCatalogEntry) {
		if (!catalog || installingId) return;
		const confirmed = await confirmDialog({
			title: `${catalogActionLabel(entry)} ${entry.name} ${entry.version}?`,
			message:
				`PotionUI downloads ${entry.archive}, checks it against the sha256 ${entry.sha256} the catalog lists, and unpacks it into content/plugins/local/${entry.id}. ` +
				'If the catalog lists another version or sha256 by then, nothing is installed. ' +
				'A plugin is code that runs with the same rights as PotionUI: install only from a catalog you trust.',
			variant: 'warning',
			confirmLabel: catalogActionLabel(entry) ?? 'Install'
		});
		if (!confirmed) return;
		installingId = entry.id;
		error = null;
		installed = null;
		try {
			installed = await installFromCatalog(catalog.url, entry);
			await onInstalled(installed);
			catalog = await readPluginCatalog(catalog.url);
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			installingId = null;
		}
	}
</script>

<BaseModal
	{isOpen}
	title="Install from a catalog"
	subtitle="A catalog is a plugins.json file that lists plugins with their release archives."
	size="lg"
	on:close={onClose}
>
	<div class="flex flex-col gap-4 p-4 sm:p-5">
		<form
			class="flex items-center gap-2"
			onsubmit={(event) => {
				event.preventDefault();
				void readCatalog();
			}}
		>
			<Input
				bind:value={url}
				type="url"
				class="flex-1"
				placeholder="https://example.com/plugins.json"
				aria-label="Catalog URL"
				disabled={reading}
			/>
			<Button type="submit" variant="primary" icon={reading ? undefined : 'search'} loading={reading} disabled={reading || !url.trim()}>
				Read catalog
			</Button>
		</form>

		<Alert variant="warning" icon density="compact">
			A plugin runs with the same rights as PotionUI. Install only from a catalog you trust.
		</Alert>

		{#if error}
			<Alert variant="danger" icon title="Not done">{error}</Alert>
		{/if}

		{#if installed}
			<Alert variant="success" icon title={installed.previous_version ? 'Plugin updated' : 'Plugin installed'}>
				{installed.plugin_id}
				{installed.previous_version ? `${installed.previous_version} → ${installed.version}` : installed.version}
				{installed.enabled ? 'is running.' : 'is in the list, disabled until you enable it.'}
				{#if installed.dependencies_python.length > 0}
					It declares the Python packages {installed.dependencies_python.join(', ')}; PotionUI does not install them, and enabling the
					plugin checks that they are there.
				{/if}
			</Alert>
		{/if}

		{#if catalog}
			{#if catalog.problems.length > 0}
				<Alert variant="warning" icon title="Entries set aside">
					<ul class="list-disc pl-4">
						{#each catalog.problems as problem (problem.index)}
							<li>{problem.plugin_id ?? `Entry ${problem.index + 1}`}: {problem.reason}</li>
						{/each}
					</ul>
				</Alert>
			{/if}

			{#if catalog.entries.length === 0}
				<p class="text-sm text-fg-muted">This catalog lists no plugin.</p>
			{:else}
				<ul class="flex flex-col gap-2" aria-label="Catalog plugins">
					{#each catalog.entries as entry (entry.id)}
						{@const action = catalogActionLabel(entry)}
						<li class="flex items-start justify-between gap-3 rounded-lg border border-line bg-surface-2 p-3" data-plugin-id={entry.id}>
							<div class="min-w-0 flex-1">
								<div class="flex flex-wrap items-center gap-2">
									<span class="font-medium text-fg">{entry.name}</span>
									<span class="text-sm text-fg-muted">{entry.version}</span>
									<Badge variant={catalogStatusTone(entry.status)} size="sm">{catalogStatusLabel(entry.status)}</Badge>
								</div>
								<p class="text-sm text-fg-muted">
									{entry.id}{entry.license ? ` · ${entry.license}` : ''} · core {entry.requires_core}{entry.installed_version
										? ` · installed ${entry.installed_version}`
										: ''}
								</p>
								{#if entry.description}
									<p class="mt-1 text-sm text-fg-muted">{entry.description}</p>
								{/if}
								{#if entry.reason}
									<p class="mt-1 text-sm text-fg-muted">{entry.reason}</p>
								{/if}
							</div>
							{#if action}
								<Button
									variant="secondary"
									size="sm"
									loading={installingId === entry.id}
									disabled={installingId !== null}
									onclick={() => install(entry)}
								>
									{action}
								</Button>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
</BaseModal>
