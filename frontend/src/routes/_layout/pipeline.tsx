import { createFileRoute } from '@tanstack/react-router'
import PipelineView from '../../components/PipelineView'

export const Route = createFileRoute('/_layout/pipeline')({
  component: PipelineView,
})
