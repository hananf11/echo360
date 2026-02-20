import { BrowserRouter, Routes, Route } from 'react-router-dom'
import CourseLibrary from './components/CourseLibrary'
import CourseDetail from './components/CourseDetail'

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-slate-900 text-slate-100">
        <Routes>
          <Route path="/" element={<CourseLibrary />} />
          <Route path="/courses/:id" element={<CourseDetail />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
