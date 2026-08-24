/** @type {import('tailwindcss').Config} */

// Semantic tokens live in src/lib/styles/tokens.css as RGB triplets;
// this helper wires them up so alpha utilities work (bg-surface-1/50 etc).
const t = (v) => `rgb(var(--${v}) / <alpha-value>)`;

export default {
	content: ['./src/**/*.{html,js,svelte,ts}', './node_modules/layerchart/**/*.{svelte,js}'],
	theme: {
		extend: {
			colors: {
				canvas: t('canvas'),
				surface: { 1: t('surface-1'), 2: t('surface-2'), 3: t('surface-3') },
				fg: {
					DEFAULT: t('fg'),
					muted: t('fg-muted'),
					subtle: t('fg-subtle'),
					disabled: t('fg-disabled')
				},
				line: { DEFAULT: t('line'), strong: t('line-strong'), hover: t('line-hover') },
				field: { bg: t('field-bg'), border: t('field-border') },
				accent: {
					DEFAULT: t('accent'),
					hover: t('accent-hover'),
					active: t('accent-active'),
					contrast: t('accent-contrast')
				},
				signal: { DEFAULT: t('signal'), solid: t('signal-solid') },
				success: { DEFAULT: t('success'), solid: t('success-solid') },
				danger: { DEFAULT: t('danger'), solid: t('danger-solid') },
				warning: { DEFAULT: t('warning'), solid: t('warning-solid') },
				info: { DEFAULT: t('info'), solid: t('info-solid') },
				ai: { 1: t('ai-1'), 2: t('ai-2'), 3: t('ai-3') },
				viz: {
					1: t('viz-1'),
					2: t('viz-2'),
					3: t('viz-3'),
					4: t('viz-4'),
					5: t('viz-5'),
					6: t('viz-6'),
					7: t('viz-7'),
					8: t('viz-8'),
					grid: t('viz-grid'),
					axis: t('viz-axis')
				}
			},
			height: { header: 'var(--header-h)' },
			minHeight: { header: 'var(--header-h)' },
			boxShadow: {
				raised: 'var(--shadow-raised)',
				floating: 'var(--shadow-floating)',
				overlay: 'var(--shadow-overlay)',
				well: 'var(--shadow-well)',
				'active-glow': '0 0 10px rgb(var(--accent) / 0.12)'
			}
		},
		// Machined radius scale: 4px controls, 6px cards, 10px overlays.
		borderRadius: {
			none: '0',
			sm: '2px',
			DEFAULT: '4px',
			md: '4px',
			lg: '6px',
			xl: '10px',
			'2xl': '10px',
			'3xl': '14px',
			full: '9999px'
		},
		// Dense precision-tool type scale (13px base).
		fontSize: {
			'2xs': ['10px', { lineHeight: '14px' }],
			xs: ['11px', { lineHeight: '16px' }],
			sm: ['12px', { lineHeight: '16px' }],
			base: ['13px', { lineHeight: '20px' }],
			md: ['14px', { lineHeight: '20px' }],
			lg: ['16px', { lineHeight: '24px' }],
			xl: ['20px', { lineHeight: '28px' }],
			'2xl': ['24px', { lineHeight: '32px' }],
			'3xl': ['30px', { lineHeight: '36px' }],
			'4xl': ['36px', { lineHeight: '40px' }],
			'5xl': ['48px', { lineHeight: '1' }],
			'6xl': ['60px', { lineHeight: '1' }]
		},
		fontFamily: {
			sans: [
				'-apple-system',
				'BlinkMacSystemFont',
				'"Segoe UI Variable"',
				'"Segoe UI"',
				'system-ui',
				'Roboto',
				'"Helvetica Neue"',
				'Arial',
				'sans-serif'
			],
			mono: [
				'"IBM Plex Mono"',
				'ui-monospace',
				'SFMono-Regular',
				'"Cascadia Mono"',
				'Menlo',
				'Consolas',
				'monospace'
			]
		}
	},
	plugins: []
};
