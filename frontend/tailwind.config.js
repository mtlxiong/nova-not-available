/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#090b10',
        panel: '#12141a',
        paper: '#eceae4',
        mute: '#9a968c',
        brass: '#e4c48a',
      },
      fontFamily: {
        sans: ['Manrope', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['IBM Plex Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        panel: '0 18px 50px rgba(0, 0, 0, 0.35)',
      },
    },
  },
  plugins: [],
}
