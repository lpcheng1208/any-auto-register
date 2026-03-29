import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { TableColumnsType } from 'antd'
import { DeleteOutlined, ReloadOutlined, RetweetOutlined, StopOutlined } from '@ant-design/icons'
import { apiFetch, API_BASE } from '@/lib/utils'

const { Text, Paragraph } = Typography

type TaskStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'partial_success'
  | 'failed'
  | 'cancel_requested'
  | 'cancelled'
  | 'interrupted'

interface TaskItem {
  id: string
  task_id: string
  task_type: string
  trigger_source: string
  status: TaskStatus
  platform?: string | null
  progress: string
  processed_count: number
  total_count: number
  success: number
  failed: number
  errors: string[]
  summary: Record<string, unknown>
  error: string
  cashier_urls: string[]
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  updated_at: string
  parent_task_id?: string | null
}

interface PlatformOption {
  value: string
  label: string
}

interface PlatformResponseItem {
  name: string
  display_name: string
}

interface TaskListResponse {
  total: number
  items: TaskItem[]
}

interface TaskEventItem {
  id: number
  level: string
  message: string
  created_at: string
}

interface TaskEventsResponse {
  items: TaskEventItem[]
}

interface TaskLogItem {
  id: number
  task_id: string
  item_type: string
  item_key: string
  platform: string
  email: string
  status: string
  error: string
  detail_json: string
  created_at: string
}

interface TaskItemsResponse {
  total: number
  items: TaskLogItem[]
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  success: 'success',
  partial_success: 'warning',
  failed: 'error',
  cancel_requested: 'warning',
  cancelled: 'default',
  interrupted: 'error',
}

const STATUS_LABELS: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  success: '成功',
  partial_success: '部分成功',
  failed: '失败',
  cancel_requested: '取消中',
  cancelled: '已取消',
  interrupted: '已中断',
}

const TASK_TYPE_LABELS: Record<string, string> = {
  register_batch: '注册任务',
  account_check_batch: '账号检测',
  proxy_check_batch: '代理检测',
  scheduler_trial_expiry: 'Trial 到期检查',
}

const DEFAULT_PLATFORM_OPTIONS: PlatformOption[] = [
  { value: 'trae', label: 'Trae.ai' },
  { value: 'cursor', label: 'Cursor' },
  { value: 'kiro', label: 'Kiro' },
  { value: 'grok', label: 'Grok' },
  { value: 'chatgpt', label: 'ChatGPT' },
  { value: 'openblocklabs', label: 'OpenBlockLabs' },
]

function TaskDetailDrawer({
  task,
  open,
  onClose,
}: {
  task: TaskItem | null
  open: boolean
  onClose: () => void
}) {
  const [events, setEvents] = useState<TaskEventItem[]>([])
  const [items, setItems] = useState<TaskLogItem[]>([])
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const latestEventIdRef = useRef(0)

  const loadDetail = useCallback(async () => {
    if (!task) return
    setLoading(true)
    try {
      const [eventData, itemData] = await Promise.all([
        apiFetch(`/tasks/${task.id}/events`) as Promise<TaskEventsResponse>,
        apiFetch(`/tasks/${task.id}/items?page=1&page_size=100`) as Promise<TaskItemsResponse>,
      ])
      setEvents(eventData.items || [])
      setItems(itemData.items || [])
    } finally {
      setLoading(false)
    }
  }, [task])

  useEffect(() => {
    if (open && task) {
      loadDetail()
    }
  }, [open, task, loadDetail])

  useEffect(() => {
    latestEventIdRef.current = events.length > 0 ? Math.max(...events.map((event) => event.id || 0)) : 0
  }, [events])

  useEffect(() => {
    if (!open || !task) return
    if (['success', 'partial_success', 'failed', 'cancelled', 'interrupted'].includes(task.status)) {
      return
    }

    const since = latestEventIdRef.current
    const es = new EventSource(`${API_BASE}/tasks/${task.id}/logs/stream?since=${since}`)

    es.onmessage = (e) => {
      const data = JSON.parse(e.data)
      if (data.line && data.id) {
        setEvents((prev) => {
          if (prev.some((event) => event.id === data.id)) return prev
          return [
            ...prev,
            {
              id: data.id,
              level: data.line.includes('✗') || data.line.includes('失败') || data.line.includes('异常')
                ? 'error'
                : data.line.includes('warning') || data.line.includes('失效')
                  ? 'warning'
                  : 'info',
              message: data.line,
              created_at: new Date().toISOString(),
            },
          ]
        })
      }
      if (data.done) {
        es.close()
        loadDetail()
      }
    }

    es.onerror = () => {
      es.close()
    }

    return () => es.close()
  }, [open, task, loadDetail])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <Drawer
      title={task ? `任务详情 · ${TASK_TYPE_LABELS[task.task_type] || task.task_type}` : '任务详情'}
      open={open}
      onClose={onClose}
      width={880}
    >
      {task && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="任务 ID" span={2}>
              <Text copyable style={{ fontFamily: 'monospace' }}>{task.id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="类型">{TASK_TYPE_LABELS[task.task_type] || task.task_type}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={STATUS_COLORS[task.status] || 'default'}>{STATUS_LABELS[task.status] || task.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="平台">{task.platform || '-'}</Descriptions.Item>
            <Descriptions.Item label="来源">{task.trigger_source}</Descriptions.Item>
            <Descriptions.Item label="进度">{task.progress}</Descriptions.Item>
            <Descriptions.Item label="结果汇总">成功 {task.success} / 失败 {task.failed}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{task.finished_at ? new Date(task.finished_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
          </Descriptions>

          {task.error && (
            <Card size="small" title="任务错误">
              <Paragraph style={{ marginBottom: 0 }}>{task.error}</Paragraph>
            </Card>
          )}

          <Card size="small" title={`任务明细 (${items.length})`} loading={loading}>
            <Table
              rowKey="id"
              size="small"
              pagination={{ pageSize: 5, showSizeChanger: false }}
              dataSource={items}
              columns={[
                {
                  title: '对象',
                  dataIndex: 'item_key',
                  key: 'item_key',
                  render: (value: string, record: TaskLogItem) => value || record.email || '-',
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  key: 'status',
                  width: 120,
                  render: (value: string) => <Tag color={value === 'success' ? 'success' : 'error'}>{value}</Tag>,
                },
                {
                  title: '错误',
                  dataIndex: 'error',
                  key: 'error',
                  render: (value: string) => value || '-',
                },
                {
                  title: '时间',
                  dataIndex: 'created_at',
                  key: 'created_at',
                  width: 180,
                  render: (value: string) => (value ? new Date(value).toLocaleString('zh-CN') : '-'),
                },
              ]}
            />
          </Card>

          <Card size="small" title={`执行日志 (${events.length})`} loading={loading}>
            <div
              style={{
                maxHeight: 320,
                overflow: 'auto',
                background: 'rgba(0,0,0,0.45)',
                borderRadius: 8,
                padding: 12,
                fontFamily: 'monospace',
                fontSize: 12,
                whiteSpace: 'pre-wrap',
              }}
            >
              {events.length === 0 && <div style={{ color: '#7a8ba3' }}>暂无日志</div>}
              {events.map((event) => (
                <div
                  key={event.id}
                  style={{
                    color:
                      event.level === 'error'
                        ? '#ef4444'
                        : event.level === 'warning'
                          ? '#f59e0b'
                          : '#b0bcd4',
                    marginBottom: 6,
                  }}
                >
                  {event.message}
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
          </Card>
        </div>
      )}
    </Drawer>
  )
}

export default function TaskHistory() {
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [total, setTotal] = useState(0)
  const [platform, setPlatform] = useState('')
  const [status, setStatus] = useState('')
  const [taskType, setTaskType] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [detailTask, setDetailTask] = useState<TaskItem | null>(null)
  const [platformOptions, setPlatformOptions] = useState<PlatformOption[]>(DEFAULT_PLATFORM_OPTIONS)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: '1', page_size: '20' })
      if (platform) params.set('platform', platform)
      if (status) params.set('status', status)
      if (taskType) params.set('task_type', taskType)
      const data = await apiFetch(`/tasks?${params}`) as TaskListResponse
      setTasks(data.items || [])
      setTotal(data.total || 0)
      setSelectedRowKeys((prev) => prev.filter((key) => data.items.some((item) => item.id === key)))
    } finally {
      setLoading(false)
    }
  }, [platform, status, taskType])

  const loadPlatforms = useCallback(async () => {
    try {
      const data = await apiFetch('/platforms') as PlatformResponseItem[]
      const nextOptions = data
        .filter((item) => item.name !== 'tavily')
        .map((item) => ({ value: item.name, label: item.display_name || item.name }))

      if (nextOptions.length > 0) {
        setPlatformOptions(nextOptions)
      }
    } catch {
      setPlatformOptions(DEFAULT_PLATFORM_OPTIONS)
    }
  }, [])

  useEffect(() => {
    loadPlatforms()
  }, [loadPlatforms])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [load])

  const selectedTasks = useMemo(
    () => tasks.filter((task) => selectedRowKeys.includes(task.id)),
    [tasks, selectedRowKeys],
  )

  const activeSelectedTasks = selectedTasks.filter((task) => ['pending', 'running', 'cancel_requested'].includes(task.status))

  const handleBatchDelete = async () => {
    if (selectedTasks.length === 0) return
    if (activeSelectedTasks.length > 0) {
      message.error('运行中的任务不能删除')
      return
    }
    await Promise.all(selectedTasks.map((task) => apiFetch(`/tasks/${task.id}`, { method: 'DELETE' })))
    message.success(`已删除 ${selectedTasks.length} 个任务`)
    setSelectedRowKeys([])
    await load()
  }

  const handleCancel = async (task: TaskItem) => {
    await apiFetch(`/tasks/${task.id}/cancel`, { method: 'POST' })
    message.success('已发送取消请求')
    await load()
  }

  const handleRetry = async (task: TaskItem) => {
    const result = await apiFetch(`/tasks/${task.id}/retry`, {
      method: 'POST',
      body: JSON.stringify({ inherit_payload: true }),
    })
    message.success(`已创建重试任务 ${result.task_id}`)
    await load()
  }

  const columns: TableColumnsType<TaskItem> = [
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (value: string) => (value ? new Date(value).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '任务类型',
      dataIndex: 'task_type',
      key: 'task_type',
      width: 140,
      render: (value: string) => TASK_TYPE_LABELS[value] || value,
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 100,
      render: (value?: string | null) => (value ? <Tag>{value}</Tag> : '-'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (value: string) => <Tag color={STATUS_COLORS[value] || 'default'}>{STATUS_LABELS[value] || value}</Tag>,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 120,
    },
    {
      title: '结果',
      key: 'summary',
      width: 150,
      render: (_, record) => `成功 ${record.success} / 失败 ${record.failed}`,
    },
    {
      title: '错误摘要',
      key: 'error',
      render: (_, record) => record.error || record.errors?.[0] || '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => setDetailTask(record)}>
            详情
          </Button>
          {['pending', 'running', 'cancel_requested'].includes(record.status) && (
            <Button type="link" size="small" icon={<StopOutlined />} onClick={() => handleCancel(record)}>
              取消
            </Button>
          )}
          {['failed', 'partial_success', 'cancelled', 'interrupted'].includes(record.status) && (
            <Button type="link" size="small" icon={<RetweetOutlined />} onClick={() => handleRetry(record)}>
              重试
            </Button>
          )}
          {!['pending', 'running', 'cancel_requested'].includes(record.status) && (
            <Popconfirm title="确认删除该任务？" onConfirm={() => apiFetch(`/tasks/${record.id}`, { method: 'DELETE' }).then(() => load())}>
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>任务中心</h1>
          <p style={{ color: '#7a8ba3', marginTop: 4 }}>统一查看注册、检测和系统任务</p>
        </div>
        <Space wrap>
          <Text type="secondary">{total} 个任务</Text>
          {selectedRowKeys.length > 0 && <Text type="success">已选 {selectedRowKeys.length} 个</Text>}
          {selectedRowKeys.length > 0 && (
            <Popconfirm title={`确认删除选中的 ${selectedRowKeys.length} 个任务？`} onConfirm={handleBatchDelete}>
              <Button danger icon={<DeleteOutlined />} disabled={activeSelectedTasks.length > 0}>
                删除 {selectedRowKeys.length} 个
              </Button>
            </Popconfirm>
          )}
          <Select
            value={taskType}
            onChange={(value) => setTaskType(value)}
            style={{ width: 140 }}
            options={[
              { value: '', label: '全部类型' },
              { value: 'register_batch', label: '注册任务' },
              { value: 'account_check_batch', label: '账号检测' },
              { value: 'proxy_check_batch', label: '代理检测' },
              { value: 'scheduler_trial_expiry', label: '系统任务' },
            ]}
          />
          <Select
            value={status}
            onChange={(value) => setStatus(value)}
            style={{ width: 140 }}
            options={[
              { value: '', label: '全部状态' },
              { value: 'pending', label: '待执行' },
              { value: 'running', label: '执行中' },
              { value: 'cancel_requested', label: '取消中' },
              { value: 'success', label: '成功' },
              { value: 'partial_success', label: '部分成功' },
              { value: 'failed', label: '失败' },
              { value: 'cancelled', label: '已取消' },
              { value: 'interrupted', label: '已中断' },
            ]}
          />
          <Select
            value={platform}
            onChange={(value) => setPlatform(value)}
            style={{ width: 160 }}
            options={[{ value: '', label: '全部平台' }, ...platformOptions]}
          />
          <Button icon={<ReloadOutlined spin={loading} />} onClick={load} loading={loading} />
        </Space>
      </div>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={tasks}
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as string[]),
          }}
          pagination={{ pageSize: 20, showSizeChanger: false }}
        />
      </Card>

      <TaskDetailDrawer task={detailTask} open={!!detailTask} onClose={() => setDetailTask(null)} />
    </div>
  )
}
