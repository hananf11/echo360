import { createFileRoute } from '@tanstack/react-router'
import CourseDetail from '../components/CourseDetail'

export const Route = createFileRoute('/courses/$id')({
  component: CourseDetailRoute,
})

function CourseDetailRoute() {
  const { id } = Route.useParams()
  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      <CourseDetail courseId={Number(id)} />
    </div>
  )
}
