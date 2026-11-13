const shapes = {
  plus: <path d="M12 5v14M5 12h14" />,
  send: <path d="m4 12 16-7-4 15-4-6-8-2Zm8 2 8-9" />,
  expand: <path d="M14 5h5v5m0-5-7 7M10 19H5v-5m0 5 7-7" />,
}

export function Icon({ name, size = 20 }: { name: keyof typeof shapes; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>
}
