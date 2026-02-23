import { createFileRoute } from '@tanstack/react-router'
import CourseLibrary from '../../components/CourseLibrary'

export const Route = createFileRoute('/_layout/')({
  component: CourseLibrary,
})
