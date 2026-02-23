import { createFileRoute } from '@tanstack/react-router'
import CourseDetail from '../../components/CourseDetail'

export const Route = createFileRoute('/_layout/courses/$id')({
  component: CourseDetailRoute,
})

function CourseDetailRoute() {
  const { id } = Route.useParams()
  return <CourseDetail courseId={Number(id)} />
}
