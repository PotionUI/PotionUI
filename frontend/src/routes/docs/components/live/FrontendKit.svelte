<script lang="ts">
	import {
		Badge,
		Button,
		Card,
		EmptyState,
		IconButton,
		Input,
		Kbd,
		PageContainer,
		PageHeader,
		Spinner
	} from '$lib/components/ui';
	import Logo from '$lib/components/brand/Logo.svelte';
	import LogoLockup from '$lib/components/brand/LogoLockup.svelte';
	import ComponentExample from './ComponentExample.svelte';
	import ApplicationCardsReference from './ApplicationCardsReference.svelte';
	import InteractiveComponentsReference from './InteractiveComponentsReference.svelte';
	import LayoutDataReference from './LayoutDataReference.svelte';
	import SchemaPromptReference from './SchemaPromptReference.svelte';

	let inputValue = '';

</script>

<div class="space-y-8">
	<div class="border-b border-line pb-5">
		<p class="text-sm text-fg-muted max-w-3xl">
			The canonical shared components for application UI. Use these before creating route-local
			buttons, cards, headers, loading indicators, or empty states. Every example is live and uses
			the same semantic tokens as the application.
		</p>
		<div class="flex flex-wrap gap-2 mt-4">
			<Badge variant="signal">11 UI primitives</Badge>
			<Badge variant="info">8 interactive controls</Badge>
			<Badge variant="success">8 domain-card states</Badge>
			<Badge variant="warning">Prompt and schema states</Badge>
			<Badge variant="neutral">4 data/layout components</Badge>
		</div>
		<nav class="flex flex-wrap gap-4 mt-4 text-sm" aria-label="Frontend kit sections">
			<a class="text-signal hover:underline" href="#kit-foundations">Foundations</a>
			<a class="text-signal hover:underline" href="#kit-interactions">Interactions</a>
			<a class="text-signal hover:underline" href="#kit-cards">Application cards</a>
			<a class="text-signal hover:underline" href="#kit-schema-prompts">Fields and prompts</a>
			<a class="text-signal hover:underline" href="#kit-layouts">Data and layouts</a>
			<a class="text-signal hover:underline" href="#kit-tokens">Tokens</a>
		</nav>
	</div>

	<section id="kit-foundations" class="space-y-8 scroll-mt-4">
		<div>
			<h2 class="text-xl font-semibold text-fg">Foundations and primitives</h2>
			<p class="text-sm text-fg-muted mt-1">The complete public export surface of <code class="font-mono text-xs">$lib/components/ui</code>.</p>
		</div>
	<ComponentExample
		title="Button"
		description="Primary is reserved for the main action; state and navigation use signal styling elsewhere."
		code={`<Button variant="primary" icon="plus">Create</Button>\n<Button variant="secondary">Cancel</Button>\n<Button variant="ghost">Details</Button>\n<Button variant="danger">Delete</Button>\n<Button loading>Loading</Button>`}
	>
		<Button variant="primary" icon="plus">Create</Button>
		<Button variant="secondary">Cancel</Button>
		<Button variant="ghost">Details</Button>
		<Button variant="danger">Delete</Button>
		<Button loading>Loading</Button>
	</ComponentExample>

	<ComponentExample
		title="IconButton"
		description="For icon-only actions. A label is mandatory and becomes the accessible name and tooltip."
		code={`<IconButton icon="refresh" label="Refresh" />\n<IconButton icon="star" label="Favorite" active />\n<IconButton icon="trash" label="Unavailable" disabled />`}
	>
		<IconButton icon="refresh" label="Refresh" />
		<IconButton icon="star" label="Favorite" active />
		<IconButton icon="trash" label="Unavailable" disabled />
	</ComponentExample>

	<ComponentExample
		title="Badge"
		description="Compact status and metadata labels."
		code={`<Badge>Draft</Badge>\n<Badge variant="success" dot>Ready</Badge>\n<Badge variant="warning">Queued</Badge>\n<Badge variant="danger">Failed</Badge>\n<Badge variant="signal">Selected</Badge>`}
	>
		<Badge>Draft</Badge>
		<Badge variant="success" dot>Ready</Badge>
		<Badge variant="warning">Queued</Badge>
		<Badge variant="danger">Failed</Badge>
		<Badge variant="signal">Selected</Badge>
	</ComponentExample>

	<ComponentExample
		title="Logo"
		description="Use the shared mark and wordmark instead of embedding brand SVGs in routes."
		code={`<Logo size={28} />\n<LogoLockup size={28} />\n<LogoLockup size={28} stacked />`}
	>
		<Logo size={28} />
		<LogoLockup size={28} />
		<LogoLockup size={28} stacked />
	</ComponentExample>

	<ComponentExample
		title="PageContainer"
		description="Responsive page gutters and canonical maximum widths."
		code={`<PageContainer width="md">Page content</PageContainer>`}
	>
		<div class="w-full border border-line rounded overflow-hidden">
			<PageContainer width="md" class="bg-surface-1 py-3">Responsive page content</PageContainer>
		</div>
	</ComponentExample>

	<ComponentExample
		title="Card"
		description="The shared surface container; interactive cards add hover and keyboard behavior."
		code={`<Card>Static content</Card>\n<Card interactive onclick={openDetails}>Interactive content</Card>`}
	>
		<Card class="w-full sm:w-64">
			<p class="text-sm font-semibold text-fg">Static card</p>
			<p class="text-xs text-fg-muted mt-1">Grouped information without an action.</p>
		</Card>
		<Card interactive class="w-full sm:w-64" onclick={() => undefined}>
			<p class="text-sm font-semibold text-fg">Interactive card</p>
			<p class="text-xs text-fg-muted mt-1">Focus and press Enter or Space.</p>
		</Card>
	</ComponentExample>

	<ComponentExample
		title="Input and keyboard hint"
		description="Inputs share one focus, border, and disabled-state contract."
		code={`<Input bind:value placeholder="Search..." />\n<Kbd keys={["Ctrl", "K"]} />`}
	>
		<div class="w-full sm:max-w-sm">
			<label class="label" for="kit-search">Search</label>
			<Input id="kit-search" type="search" bind:value={inputValue} placeholder="Search components..." />
		</div>
		<Kbd keys={['Ctrl', 'K']} />
	</ComponentExample>

	<ComponentExample
		title="Loading"
		description="Use a spinner for indeterminate work and preserve surrounding layout while loading."
		code={`<Spinner size="sm" />\n<Spinner size="md" />\n<Spinner size="lg" />`}
	>
		<Spinner size="sm" />
		<Spinner size="md" />
		<Spinner size="lg" />
	</ComponentExample>

	<ComponentExample
		title="PageHeader"
		description="All standard pages use this title-left, actions-right structure. It wraps on narrow screens."
		code={`<PageHeader title="Models" description="Manage available models" sticky={false}>\n  {#snippet actions()}<Button variant="primary">Add model</Button>{/snippet}\n</PageHeader>`}
	>
		<div class="w-full border border-line rounded-lg overflow-hidden">
			<PageHeader title="Models" description="Manage available models" sticky={false}>
				{#snippet actions()}<Button variant="primary" size="sm" icon="plus">Add model</Button>{/snippet}
			</PageHeader>
			<div class="h-16 bg-canvas"></div>
		</div>
	</ComponentExample>

	<ComponentExample
		title="EmptyState"
		description="A consistent zero-data or zero-result state with an optional recovery action."
		code={`<EmptyState title="No models" description="Add a model to get started." icon="model">\n  {#snippet actions()}<Button variant="primary">Add model</Button>{/snippet}\n</EmptyState>`}
	>
		<div class="w-full">
			<EmptyState title="No models" description="Add a model to get started." icon="model" compact>
				{#snippet actions()}<Button variant="primary" size="sm">Add model</Button>{/snippet}
			</EmptyState>
		</div>
	</ComponentExample>
	</section>

	<section id="kit-interactions" class="scroll-mt-4">
		<h2 class="text-xl font-semibold text-fg mb-1">Interactive components</h2>
		<p class="text-sm text-fg-muted mb-4">Reusable application controls with local state and real interaction behavior.</p>
		<InteractiveComponentsReference />
	</section>

	<section id="kit-cards" class="scroll-mt-4">
		<h2 class="text-xl font-semibold text-fg mb-1">Application cards</h2>
		<p class="text-sm text-fg-muted mb-4">The production cards rendered against realistic, local fixture data across their important states.</p>
		<ApplicationCardsReference />
	</section>

	<section id="kit-schema-prompts" class="scroll-mt-4">
		<h2 class="text-xl font-semibold text-fg mb-1">Schema fields and prompt segments</h2>
		<p class="text-sm text-fg-muted mb-4">Production rendering examples driven by local schema and segment fixtures, including the states contributors should verify when changing these components.</p>
		<SchemaPromptReference />
	</section>

	<section id="kit-layouts" class="scroll-mt-4">
		<h2 class="text-xl font-semibold text-fg mb-1">Data and layouts</h2>
		<p class="text-sm text-fg-muted mb-4">Operational metrics, accessible chart containers, and the responsive master-detail pattern.</p>
		<LayoutDataReference />
	</section>

	<section id="kit-tokens" class="border border-line rounded-lg overflow-hidden scroll-mt-4">
		<div class="px-4 py-3 bg-surface-1 border-b border-line">
			<h2 class="text-sm font-semibold text-fg">Semantic tokens</h2>
			<p class="text-xs text-fg-muted mt-1">Never use raw palette classes in application components.</p>
		</div>
		<div class="grid grid-cols-2 sm:grid-cols-4 gap-px bg-line">
			{#each [
				['Canvas', 'bg-canvas'], ['Surface 1', 'bg-surface-1'], ['Surface 2', 'bg-surface-2'], ['Surface 3', 'bg-surface-3'],
				['Action', 'bg-accent'], ['Selection', 'bg-signal'], ['Success', 'bg-success'], ['Danger', 'bg-danger']
			] as token}
				<div class="bg-canvas p-3">
					<div class="h-10 rounded border border-line {token[1]}"></div>
					<p class="font-mono text-2xs text-fg-muted mt-2">{token[0]}</p>
					<code class="font-mono text-2xs text-fg-subtle">{token[1]}</code>
				</div>
			{/each}
		</div>
	</section>
</div>
