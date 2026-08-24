# Image Modal Viewer Plugin

A PotionUI plugin that adds a full-screen image viewer modal to the workbench.

## Features

- Full-screen image viewing
- Click to open, ESC or click to close
- Smooth animations
- Responsive design

## Installation

The plugin is already included in the PotionUI plugins directory.

1. Scan for plugins via the admin panel or API
2. Enable the plugin

## Development

### Building the Frontend Component

The plugin uses Svelte for its frontend component. To build the component:

```bash
cd frontend
npm install
npm run build
```

This will compile `src/ImageModalAction.svelte` to `dist/ImageModalAction.js`, which is served by the PotionUI API.

### Project Structure

```
image-modal/
├── manifest.yml              # Plugin configuration
├── frontend/
│   ├── src/
│   │   └── ImageModalAction.svelte  # Source component
│   ├── dist/
│   │   └── ImageModalAction.js      # Built component (served to browser)
│   ├── package.json
│   └── build.js              # Build script
└── README.md                 # This file
```

### Build Configuration

The build script (`build.js`) uses esbuild with the following configuration:

- **Bundle**: All dependencies are bundled into a single file
- **Format**: ES modules (ESM)
- **External**: None (Svelte runtime is bundled)
- **CSS**: Injected into the component
- **Minification**: Disabled for easier debugging
- **Source Maps**: Enabled

This creates a standalone component that can be dynamically loaded by the frontend without external dependencies.

## Usage

Once enabled, the plugin adds a full-screen button to the workbench image actions. Click the button to view the current image in full-screen mode.

## Technical Details

### Dynamic Component Loading

This plugin demonstrates the dynamic Svelte component loading system:

1. The backend serves the compiled component from `frontend/dist/ImageModalAction.js`
2. The frontend's `PluginSlot` component dynamically imports the module
3. The component is instantiated with context props
4. Cleanup is handled automatically on unmount

### Component Props

The Svelte component receives these props:

- `context`: Object containing the current image and other workbench state
- `hookName`: The hook name this component is registered for
- `pluginId`: The plugin ID

### API Endpoint

Components are served via:
```
GET /api/plugins/{plugin_id}/assets/{file_path}
```

For this plugin:
```
GET /api/plugins/image-modal/assets/ImageModalAction.js
```

## License

Same as PotionUI
