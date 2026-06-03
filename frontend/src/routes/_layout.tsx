import { createFileRoute, Outlet } from '@tanstack/react-router'
import Layout from '../components/Layout'
import { useRootContext } from './__root'

export const Route = createFileRoute('/_layout')({
  component: LayoutRoute,
})

function LayoutRoute() {
  const { activeCount, onOpenQueue, sessionValid, sessionRefreshing, onOpenLogin } = useRootContext()
  return (
    <Layout activeCount={activeCount} onOpenQueue={onOpenQueue} sessionValid={sessionValid} sessionRefreshing={sessionRefreshing} onOpenLogin={onOpenLogin}>
      <Outlet />
    </Layout>
  )
}
