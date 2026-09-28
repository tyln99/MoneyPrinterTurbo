import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter, Route, Routes } from "react-router-dom"
import { Create } from "@/routes/Create"
import { EpisodeDetails } from "@/routes/EpisodeDetails"
import { Library } from "@/routes/Library"

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/create" element={<Create />} />
          <Route path="/episodes/:id" element={<EpisodeDetails />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
