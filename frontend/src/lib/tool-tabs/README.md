# Tool tabs

A tool tab is a Generate tab owned by one tool (the Image editor, the Video editor, or a plugin
tool) instead of a preset. When it is active, the whole tab area is the tool's component: no form
pane, Workbench, deck, `ContextToolbar`, layout picker or preset selector.

## The tab

`ToolTab` lives in `$lib/types/tabs` next to `GenerationTab`; `Tab` is their union and
`isToolTab` / `isGenerationTab` narrow it.

| Field | Meaning |
|---|---|
| `tool` | `'image'`, `'video'` or `'<plugin>.<id>'` |
| `source` | The `MediaRef` the tab is open on, or `null` for an empty editor |
| `state` | The tool's own JSON state (params and ids, never pixels) |
| `run` | `{ runId, progress }` of a background run, so a reload reattaches |
| `activeTool` | The sub-tool to show (Crop, Trim, ...) |
| `dirty` | Unsaved step count: the tab's warning dot and the close prompt |
| `thumbnail` | URL for the 20px tab thumbnail |

Tool tabs persist with the other tabs and in Workspaces. `activeTab` always yields a
`GenerationTab` (the last active one while a tool tab is up); `currentTab` is the real active tab
and `activeToolTab` is the active tool tab or `null`.

## Registering a tool kind

```ts
import { registerToolKind } from '$lib/tool-tabs/registry';

registerToolKind({
	tool: 'image',
	label: 'Image editor',
	description: 'Crop, resize, mask, effects',
	icon: 'sliders-horizontal',
	accepts: ['image'],
	load: () => import('./ImageEditorTab.svelte')
});
```

`component` takes a component directly, `load` a lazy import. The core kinds are registered in
`builtin.ts`; the Image and Video editor lanes replace only the `load` there. Plugin kinds come from
the manifest's `tool_tabs:` section (see `docs/plugin-api.md`) through `pluginToolTabs.ts`.

## The component contract

The component gets one prop, `host: ToolTabHost` (`types.ts`):

| Member | Use |
|---|---|
| `tabId`, `tool`, `phone` | Identity, and whether the phone layout is showing |
| `source`, `state`, `activeTool`, `run` | Current tab values; a new `host` object arrives when they change |
| `setState(next)` | Persist the tool's JSON state |
| `setDirty(edits)` | Number of unsaved steps |
| `setTitle(title)` | Rename the tab unless the user already renamed it |
| `setThumbnail(url)` | Tab thumbnail |
| `setSource(ref)` | Load media into an empty tab (pickers, drops, pastes) |
| `setActiveTool(id)`, `setRun(run)` | Keep the sub-tool and the background run in the tab |
| `setActions({ undo, redo, save })` | Fill the phone top bar's Undo / Redo / Save; `save.progress` shows the render % |
| `openToolTab(tool, source, { activeTool })` | Open another tool tab (for example a video frame in the Image editor) |

## Opening tabs

`openToolTab(tool, source, { activeTool })` and `openToolTabs(tool, sources)` in `open.ts`:

- the item is already open in that tool: its tab gets focus and `activeTool` switches;
- an empty tab of that tool exists: the item loads into it (the active one first);
- otherwise a new tab opens. N items open N tabs with the first one active.

`findToolTabFor(source, tool?)` returns the tab open on an item. `ensureGenerationTabActive()`
switches to (or creates) a generation tab before anything writes generation settings.

## Files

- `types.ts`: the contract. `registry.ts`, `builtin.ts`, `pluginToolTabs.ts`: tool kinds.
- `ToolTabHost.svelte`: mounts the active tool. `PhoneToolTabView.svelte`: phone top bar + host.
- `NewTabMenu.svelte`: the "+" menu. `TabsSheet.svelte`: the phone Tabs sheet.
- `placeholders/`: the source-or-empty placeholders the editor lanes replace.
