import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const backendPort = process.env.BACKEND_PORT || '8005';
const frontendPort = parseInt(process.env.FRONTEND_PORT || '3001', 10);
// 'localhost' and '127.0.0.1' are passed through literally so the caller's
// bind address and readiness probe stay the exact same address (the
// `./potionui` CLI sets '127.0.0.1' for both, avoiding the case where a
// resolver returns ::1 ahead of 127.0.0.1 for the bare name "localhost" and
// Vite ends up IPv6-only while something else probes the IPv4 loopback).
// Any other value (including unset) keeps the previous LAN-reachable default.
const rawViteHost = process.env.VITE_HOST;
const viteHost: string | boolean =
	rawViteHost === 'localhost' || rawViteHost === '127.0.0.1' ? rawViteHost : true;

// `vite preview` (used by the browser-UI E2E harness, scripts/ui_journeys) runs
// against a throwaway backend on an ephemeral port passed in E2E_BACKEND_PORT;
// its port is E2E_PREVIEW_PORT. This block only affects the preview server —
// the dev `server` config above is untouched.
const e2eBackendPort = process.env.E2E_BACKEND_PORT || backendPort;
const previewPort = parseInt(process.env.E2E_PREVIEW_PORT || '4173', 10);

export default defineConfig({
	plugins: [sveltekit()],
	css: {
		postcss: './postcss.config.cjs'
	},
	// These three are reachable ONLY through lazily-imported routes (the admin
	// Stats/Automations/Documentation tabs), so the dev startup scan misses them.
	// The optimizer then discovers them on first visit, re-bundles, and answers
	// the in-flight request with 504 Outdated Optimize Dep - which surfaces as
	// "Failed to fetch dynamically imported module" and survives a dev-server
	// restart, because node_modules/.vite outlives the process.
	optimizeDeps: {
		include: ['layerchart', 'd3-scale', '@xyflow/svelte']
	},
	server: {
		host: viteHost,
		port: frontendPort,
		proxy: {
			// 127.0.0.1, NOT `localhost`: Node may resolve `localhost` to IPv6
			// ::1 first, and the backend listens on IPv4 only — plain HTTP
			// requests survive via happy-eyeballs retry, but raw WebSocket
			// upgrade sockets don't, failing with the browser's "WebSocket is
			// closed before the connection is established".
			'/api': {
				target: `http://127.0.0.1:${backendPort}`,
				changeOrigin: true,
				autoRewrite: true
			},
			// The backend's health check lives at `/health`, not under `/api`
			// (see GET /health in src/bootstrap/app.py). Without this, the app's
			// own relative `/health` polls (e.g. waiting for a restarted backend
			// to come back) silently hit this dev server instead - which has no
			// such route - and recovery can never be observed.
			'/health': {
				target: `http://127.0.0.1:${backendPort}`,
				changeOrigin: true
			},
			'/ws': {
				target: `ws://127.0.0.1:${backendPort}`,
				changeOrigin: true,
				ws: true
			}
		}
	},
	preview: {
		port: previewPort,
		proxy: {
			'/api': {
				target: `http://127.0.0.1:${e2eBackendPort}`,
				changeOrigin: true,
				autoRewrite: true
			},
			'/health': {
				target: `http://127.0.0.1:${e2eBackendPort}`,
				changeOrigin: true
			},
			'/ws': {
				target: `ws://127.0.0.1:${e2eBackendPort}`,
				changeOrigin: true,
				ws: true
			}
		}
	},
	test: {
		environment: 'node',
		include: ['src/**/*.{test,spec}.ts']
	}
});
