/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the Streamlit UI is served; the generation wizard still lives there. */
  readonly VITE_STREAMLIT_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
