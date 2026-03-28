import { useEffect, useMemo, useState } from 'react'
import { Card, Form, Input, Select, Button, message, Tabs, Space, Tag, Typography, Modal, Switch, Table, InputNumber, Popconfirm } from 'antd'
import {
  SaveOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  MailOutlined,
  SafetyOutlined,
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  PlusOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import { getExecutorOptions, normalizeExecutorForPlatform } from '@/lib/registerOptions'

const SELECT_FIELDS: Record<string, { label: string; value: string }[]> = {
  mail_provider: [
    { label: 'Laoudo（固定邮箱）', value: 'laoudo' },
    { label: 'TempMail.lol（自动生成）', value: 'tempmail_lol' },
    { label: 'DuckMail（自动生成）', value: 'duckmail' },
    { label: 'MoeMail (sall.cc)', value: 'moemail' },
    { label: 'Freemail（自建 CF Worker）', value: 'freemail' },
    { label: '215 Mail API', value: 'mail215' },
    { label: 'CF Worker（自建域名）', value: 'cfworker' },
  ],
  default_executor: [
    { label: 'API 协议（无浏览器）', value: 'protocol' },
    { label: '无头浏览器', value: 'headless' },
    { label: '有头浏览器（调试用）', value: 'headed' },
  ],
  default_captcha_solver: [
    { label: 'YesCaptcha', value: 'yescaptcha' },
    { label: '本地 Solver (Camoufox)', value: 'local_solver' },
    { label: '手动', value: 'manual' },
  ],
}

const PLATFORM_OPTIONS = [
  { value: 'trae', label: 'Trae.ai' },
  { value: 'cursor', label: 'Cursor' },
  { value: 'kiro', label: 'Kiro' },
  { value: 'grok', label: 'Grok' },
  { value: 'chatgpt', label: 'ChatGPT' },
  { value: 'openblocklabs', label: 'OpenBlockLabs' },
]

const INTERVAL_UNIT_OPTIONS = [
  { value: 'minutes', label: '分钟' },
  { value: 'hours', label: '小时' },
]

const TAB_ITEMS = [
  {
    key: 'register',
    label: '注册设置',
    icon: <ApiOutlined />,
    sections: [
      {
        title: '默认注册方式',
        desc: '控制注册任务如何执行',
        fields: [
          { key: 'default_executor', label: '执行器类型', type: 'select' },
          { key: 'default_target_count', label: '目标数量', placeholder: '例如 10', type: 'number' },
        ],
      },
    ],
  },
  {
    key: 'scheduled-register',
    label: '定时注册',
    icon: <ClockCircleOutlined />,
    sections: [],
  },
  {
    key: 'mailbox',
    label: '邮箱服务',
    icon: <MailOutlined />,
    sections: [
      {
        title: '默认邮箱服务',
        desc: '选择注册时使用的邮箱类型',
        fields: [{ key: 'mail_provider', label: '邮箱服务', type: 'select' }],
      },
      {
        title: 'Laoudo',
        desc: '固定邮箱，手动配置',
        fields: [
          { key: 'laoudo_email', label: '邮箱地址', placeholder: 'xxx@laoudo.com' },
          { key: 'laoudo_account_id', label: 'Account ID', placeholder: '563' },
          { key: 'laoudo_auth', label: 'JWT Token', placeholder: 'eyJ...', secret: true },
        ],
      },
      {
        title: 'Freemail',
        desc: '基于 Cloudflare Worker 的自建邮箱，支持管理员令牌或账号密码认证',
        fields: [
          { key: 'freemail_api_url', label: 'API URL', placeholder: 'https://mail.example.com' },
          { key: 'freemail_admin_token', label: '管理员令牌', secret: true },
          { key: 'freemail_username', label: '用户名（可选）' },
          { key: 'freemail_password', label: '密码（可选）', secret: true },
        ],
      },
      {
        title: 'MoeMail',
        desc: '自动注册账号并生成临时邮箱，默认无需配置',
        fields: [{ key: 'moemail_api_url', label: 'API URL', placeholder: 'https://sall.cc' }],
      },
      {
        title: 'TempMail.lol',
        desc: '自动生成邮箱，无需配置，需要代理访问（CN IP 被封）',
        fields: [],
      },
      {
        title: 'DuckMail',
        desc: '自动生成邮箱，随机创建账号（默认无需配置）',
        fields: [
          { key: 'duckmail_api_url', label: 'Web URL', placeholder: 'https://www.duckmail.sbs' },
          { key: 'duckmail_provider_url', label: 'Provider URL', placeholder: 'https://api.duckmail.sbs' },
          { key: 'duckmail_bearer', label: 'Bearer Token', placeholder: 'kevin273945', secret: true },
        ],
      },
      {
        title: '215 Mail API',
        desc: '基于 vip.215.im / maliapi.215.im 的 API Key 临时邮箱服务',
        fields: [
          { key: 'mail215_api_url', label: 'API URL', placeholder: 'https://maliapi.215.im/v1' },
          { key: 'mail215_api_key', label: 'API Key', placeholder: 'AC-...', secret: true },
          { key: 'mail215_domain', label: '域名（可选）', placeholder: 'public.example.com' },
          { key: 'mail215_address_prefix', label: '自定义前缀（可选）', placeholder: 'my-prefix' },
        ],
      },
      {
        title: 'CF Worker 自建邮箱',
        desc: '基于 Cloudflare Worker 的自建临时邮箱服务',
        fields: [
          { key: 'cfworker_api_url', label: 'API URL', placeholder: 'https://apimail.example.com' },
          { key: 'cfworker_admin_token', label: '管理员 Token', secret: true },
          { key: 'cfworker_domain', label: '邮箱域名', placeholder: 'example.com' },
          { key: 'cfworker_fingerprint', label: 'Fingerprint', placeholder: '6703363b...' },
        ],
      },
    ],
  },
  {
    key: 'captcha',
    label: '验证码',
    icon: <SafetyOutlined />,
    sections: [
      {
        title: '验证码服务',
        desc: '用于绕过注册页面的人机验证',
        fields: [
          { key: 'default_captcha_solver', label: '默认服务', type: 'select' },
          { key: 'yescaptcha_key', label: 'YesCaptcha Key', secret: true },
        ],
      },
    ],
  },
  {
    key: 'chatgpt',
    label: 'ChatGPT',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'CPA 面板',
        desc: '注册完成后自动上传到 CPA 管理平台',
        fields: [
          { key: 'cpa_api_url', label: 'API URL', placeholder: 'https://your-cpa.example.com' },
          { key: 'cpa_api_key', label: 'API Key', secret: true },
        ],
      },
      {
        title: 'Team Manager',
        desc: '上传到自建 Team Manager 系统',
        fields: [
          { key: 'team_manager_url', label: 'API URL', placeholder: 'https://your-tm.example.com' },
          { key: 'team_manager_key', label: 'API Key', secret: true },
        ],
      },
    ],
  },
  {
    key: 'cliproxyapi',
    label: 'CLIProxyAPI',
    icon: <ApiOutlined />,
    sections: [
      {
        title: '管理面板',
        desc: '用于 CLIProxyAPI 管理页登录',
        fields: [
          { key: 'cliproxyapi_management_key', label: '管理口令', secret: true, placeholder: '默认 cliproxyapi' },
        ],
      },
    ],
  },
  {
    key: 'grok',
    label: 'Grok',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'grok2api',
        desc: '注册成功后自动导入到 grok2api 管理后台',
        fields: [
          { key: 'grok2api_url', label: 'API URL', placeholder: 'http://127.0.0.1:7860' },
          { key: 'grok2api_app_key', label: 'App Key', secret: true },
          { key: 'grok2api_pool', label: 'Token Pool', placeholder: 'ssoBasic 或 ssoSuper' },
          { key: 'grok2api_quota', label: 'Quota（可选）', placeholder: '留空按池默认值' },
        ],
      },
    ],
  },
  {
    key: 'kiro',
    label: 'Kiro',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'Kiro Account Manager',
        desc: '注册成功后自动写入 kiro-account-manager 的 accounts.json',
        fields: [
          {
            key: 'kiro_manager_path',
            label: 'accounts.json 路径（可选）',
            placeholder: '留空则自动使用系统默认路径',
          },
          {
            key: 'kiro_manager_exe',
            label: 'Kiro Manager 可执行文件（可选）',
            placeholder: '未安装 Rust 时可填写已安装的 KiroAccountManager.exe',
          },
        ],
      },
    ],
  },
  {
    key: 'integrations',
    label: '插件',
    icon: <ApiOutlined />,
    sections: [],
  },
]

interface FieldConfig {
  key: string
  label: string
  placeholder?: string
  type?: 'select' | 'input' | 'number'
  secret?: boolean
}

interface SectionConfig {
  title: string
  desc?: string
  fields: FieldConfig[]
}

interface TabConfig {
  key: string
  label: string
  icon: React.ReactNode
  sections: SectionConfig[]
}

interface PlatformResponseItem {
  name: string
  display_name?: string
}

interface ScheduledRegisterTaskItem {
  id: number
  name: string
  enabled: boolean
  platform: string
  interval_minutes: number
  register: Record<string, any>
  next_run_at?: string | null
  last_run_at?: string | null
  last_task_id: string
  last_status: string
  last_error: string
  created_at: string
  updated_at: string
}

const COUNT_MODE_OPTIONS = [
  { value: 'fixed', label: '固定数量' },
  { value: 'dynamic', label: '动态补量' },
]

interface ScheduledRegisterListResponse {
  items: ScheduledRegisterTaskItem[]
}

function formatResultText(data: unknown) {
  if (typeof data === 'string') return data
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

function buildRegisterPayload(values: Record<string, any>) {
  return {
    platform: values.platform,
    email: values.email || null,
    password: values.password || null,
    count: values.count || 1,
    count_mode: values.count_mode || 'fixed',
    concurrency: values.concurrency || 1,
    register_delay_seconds: values.register_delay_seconds || 0,
    proxy: values.proxy || null,
    executor_type: values.executor_type,
    captcha_solver: values.captcha_solver,
    extra: {
      mail_provider: values.mail_provider,
      laoudo_auth: values.laoudo_auth,
      laoudo_email: values.laoudo_email,
      laoudo_account_id: values.laoudo_account_id,
      moemail_api_url: values.moemail_api_url,
      duckmail_api_url: values.duckmail_api_url,
      duckmail_provider_url: values.duckmail_provider_url,
      duckmail_bearer: values.duckmail_bearer,
      freemail_api_url: values.freemail_api_url,
      freemail_admin_token: values.freemail_admin_token,
      freemail_username: values.freemail_username,
      freemail_password: values.freemail_password,
      mail215_api_url: values.mail215_api_url,
      mail215_api_key: values.mail215_api_key,
      mail215_domain: values.mail215_domain,
      mail215_address_prefix: values.mail215_address_prefix,
      cfworker_api_url: values.cfworker_api_url,
      cfworker_admin_token: values.cfworker_admin_token,
      cfworker_domain: values.cfworker_domain,
      cfworker_fingerprint: values.cfworker_fingerprint,
      yescaptcha_key: values.yescaptcha_key,
      solver_url: values.solver_url,
    },
  }
}

function fillScheduleFormFromTask(item: ScheduledRegisterTaskItem) {
  const register = item.register || {}
  const extra = register.extra || {}
  return {
    name: item.name,
    enabled: item.enabled,
    interval_unit: item.interval_minutes % 60 === 0 ? 'hours' : 'minutes',
    interval_value: item.interval_minutes % 60 === 0 ? item.interval_minutes / 60 : item.interval_minutes,
    platform: register.platform || item.platform,
    email: register.email || '',
    password: register.password || '',
    count: register.count || 1,
    count_mode: register.count_mode || 'fixed',
    concurrency: register.concurrency || 1,
    register_delay_seconds: register.register_delay_seconds || 0,
    proxy: register.proxy || '',
    executor_type: register.executor_type || 'protocol',
    captcha_solver: register.captcha_solver || 'manual',
    mail_provider: extra.mail_provider || 'moemail',
    laoudo_auth: extra.laoudo_auth || '',
    laoudo_email: extra.laoudo_email || '',
    laoudo_account_id: extra.laoudo_account_id || '',
    moemail_api_url: extra.moemail_api_url || '',
    duckmail_api_url: extra.duckmail_api_url || '',
    duckmail_provider_url: extra.duckmail_provider_url || '',
    duckmail_bearer: extra.duckmail_bearer || '',
    freemail_api_url: extra.freemail_api_url || '',
    freemail_admin_token: extra.freemail_admin_token || '',
    freemail_username: extra.freemail_username || '',
    freemail_password: extra.freemail_password || '',
    mail215_api_url: extra.mail215_api_url || 'https://maliapi.215.im/v1',
    mail215_api_key: extra.mail215_api_key || '',
    mail215_domain: extra.mail215_domain || '',
    mail215_address_prefix: extra.mail215_address_prefix || '',
    cfworker_api_url: extra.cfworker_api_url || '',
    cfworker_admin_token: extra.cfworker_admin_token || '',
    cfworker_domain: extra.cfworker_domain || '',
    cfworker_fingerprint: extra.cfworker_fingerprint || '',
    yescaptcha_key: extra.yescaptcha_key || '',
    solver_url: extra.solver_url || 'http://localhost:8889',
  }
}

function ConfigField({ field }: { field: FieldConfig }) {
  const [showSecret, setShowSecret] = useState(false)
  const options = SELECT_FIELDS[field.key]
  const helpText =
    field.key === 'default_executor'
      ? '仅对支持的平台生效；当前只有 Trae 支持浏览器模式，其他平台会自动回退为纯协议。'
      : undefined

  return (
    <Form.Item label={field.label} name={field.key} extra={helpText}>
      {options ? (
        <Select options={options} style={{ width: '100%' }} />
      ) : field.type === 'number' ? (
        <InputNumber min={1} style={{ width: '100%' }} placeholder={field.placeholder} />
      ) : field.secret ? (
        <Input.Password
          placeholder={field.placeholder}
          visibilityToggle={{
            visible: !showSecret,
            onVisibleChange: setShowSecret,
          }}
          iconRender={(visible) => (visible ? <EyeOutlined /> : <EyeInvisibleOutlined />)}
        />
      ) : (
        <Input placeholder={field.placeholder} />
      )}
    </Form.Item>
  )
}

function ConfigSection({ section }: { section: SectionConfig }) {
  return (
    <Card title={section.title} extra={section.desc && <span style={{ fontSize: 12, color: '#7a8ba3' }}>{section.desc}</span>} style={{ marginBottom: 16 }}>
      {section.fields.map((field) => (
        <ConfigField key={field.key} field={field} />
      ))}
    </Card>
  )
}

function ScheduledRegisterPanel({ configValues }: { configValues: Record<string, any> }) {
  const [form] = Form.useForm()
  const [items, setItems] = useState<ScheduledRegisterTaskItem[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingItem, setEditingItem] = useState<ScheduledRegisterTaskItem | null>(null)
  const [platformOptions, setPlatformOptions] = useState(PLATFORM_OPTIONS)
  const mailProvider = Form.useWatch('mail_provider', form)
  const captchaSolver = Form.useWatch('captcha_solver', form)
  const countMode = Form.useWatch('count_mode', form)
  const platform = Form.useWatch('platform', form)
  const executorOptions = useMemo(() => getExecutorOptions(platform), [platform])

  const loadPlatforms = async () => {
    try {
      const data = await apiFetch('/platforms') as PlatformResponseItem[]
      const nextOptions = data
        .filter((item) => item.name !== 'tavily')
        .map((item) => ({ value: item.name, label: item.display_name || item.name }))
      if (nextOptions.length > 0) {
        setPlatformOptions(nextOptions)
      }
    } catch {
      setPlatformOptions(PLATFORM_OPTIONS)
    }
  }

  const load = async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/tasks/schedules') as ScheduledRegisterListResponse
      setItems(data.items || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    loadPlatforms()
  }, [])

  useEffect(() => {
    const currentExecutor = form.getFieldValue('executor_type')
    const normalizedExecutor = normalizeExecutorForPlatform(platform, currentExecutor)
    if (currentExecutor !== normalizedExecutor) {
      form.setFieldValue('executor_type', normalizedExecutor)
    }
  }, [form, platform])

  const openCreate = () => {
    setEditingItem(null)
    const defaultPlatform = platformOptions[0]?.value || 'trae'
    form.setFieldsValue({
      name: '',
      enabled: true,
      interval_unit: 'hours',
      interval_value: 1,
      platform: defaultPlatform,
      email: '',
      password: '',
      count: Number(configValues.default_target_count || 1),
      count_mode: 'fixed',
      concurrency: 1,
      register_delay_seconds: 0,
      proxy: '',
      executor_type: normalizeExecutorForPlatform(defaultPlatform, configValues.default_executor),
      captcha_solver: configValues.default_captcha_solver || 'manual',
      mail_provider: configValues.mail_provider || 'moemail',
      yescaptcha_key: configValues.yescaptcha_key || '',
      moemail_api_url: configValues.moemail_api_url || '',
      laoudo_auth: configValues.laoudo_auth || '',
      laoudo_email: configValues.laoudo_email || '',
      laoudo_account_id: configValues.laoudo_account_id || '',
      duckmail_api_url: configValues.duckmail_api_url || '',
      duckmail_provider_url: configValues.duckmail_provider_url || '',
      duckmail_bearer: configValues.duckmail_bearer || '',
      freemail_api_url: configValues.freemail_api_url || '',
      freemail_admin_token: configValues.freemail_admin_token || '',
      freemail_username: configValues.freemail_username || '',
      freemail_password: configValues.freemail_password || '',
      mail215_api_url: configValues.mail215_api_url || 'https://maliapi.215.im/v1',
      mail215_api_key: configValues.mail215_api_key || '',
      mail215_domain: configValues.mail215_domain || '',
      mail215_address_prefix: configValues.mail215_address_prefix || '',
      cfworker_api_url: configValues.cfworker_api_url || '',
      cfworker_admin_token: configValues.cfworker_admin_token || '',
      cfworker_domain: configValues.cfworker_domain || '',
      cfworker_fingerprint: configValues.cfworker_fingerprint || '',
      solver_url: 'http://localhost:8889',
    })
    setOpen(true)
  }

  const openEdit = (item: ScheduledRegisterTaskItem) => {
    setEditingItem(item)
    form.setFieldsValue(fillScheduleFormFromTask(item))
    setOpen(true)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      const payload = {
        name: values.name,
        enabled: values.enabled,
        interval_unit: values.interval_unit,
        interval_value: values.interval_value,
        register: buildRegisterPayload(values),
      }
      if (editingItem) {
        await apiFetch(`/tasks/schedules/${editingItem.id}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
        message.success('定时任务已更新')
      } else {
        await apiFetch('/tasks/schedules', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        message.success('定时任务已创建')
      }
      setOpen(false)
      await load()
    } finally {
      setSaving(false)
    }
  }

  const handleRunNow = async (item: ScheduledRegisterTaskItem) => {
    const result = await apiFetch(`/tasks/schedules/${item.id}/run`, {
      method: 'POST',
      body: JSON.stringify({ inherit_payload: true }),
    })
    message.success(`已创建任务 ${result.task_id}`)
    await load()
  }

  const handleToggle = async (item: ScheduledRegisterTaskItem) => {
    const values = fillScheduleFormFromTask(item)
    await apiFetch(`/tasks/schedules/${item.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: values.name,
        enabled: !item.enabled,
        interval_unit: values.interval_unit,
        interval_value: values.interval_value,
        register: buildRegisterPayload(values),
      }),
    })
    message.success(item.enabled ? '已停用' : '已启用')
    await load()
  }

  const handleDelete = async (item: ScheduledRegisterTaskItem) => {
    await apiFetch(`/tasks/schedules/${item.id}`, { method: 'DELETE' })
    message.success('已删除定时任务')
    await load()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card title="定时注册任务" extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增任务</Button>}>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={items}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          columns={[
            { title: '名称', dataIndex: 'name', key: 'name' },
            { title: '平台', dataIndex: 'platform', key: 'platform', render: (value: string) => <Tag>{value}</Tag> },
            { title: '间隔', key: 'interval', render: (_, record: ScheduledRegisterTaskItem) => `${record.interval_minutes} 分钟` },
            { title: '状态', key: 'enabled', render: (_, record: ScheduledRegisterTaskItem) => <Tag color={record.enabled ? 'success' : 'default'}>{record.enabled ? '已启用' : '已停用'}</Tag> },
            { title: '下次执行', dataIndex: 'next_run_at', key: 'next_run_at', render: (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '-' },
            { title: '最近执行', dataIndex: 'last_run_at', key: 'last_run_at', render: (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '-' },
            { title: '最近结果', key: 'last_status', render: (_, record: ScheduledRegisterTaskItem) => (
              <Space direction="vertical" size={0}>
                <Tag color={record.last_status === 'running' ? 'processing' : record.last_status === 'failed' ? 'error' : record.last_status === 'success' ? 'success' : 'default'}>{record.last_status || 'idle'}</Tag>
                {record.last_error ? <Typography.Text type="danger">{record.last_error}</Typography.Text> : null}
              </Space>
            ) },
            {
              title: '操作',
              key: 'actions',
              render: (_, record: ScheduledRegisterTaskItem) => (
                <Space wrap>
                  <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
                  <Button type="link" size="small" onClick={() => handleToggle(record)}>{record.enabled ? '停用' : '启用'}</Button>
                  <Button type="link" size="small" icon={<PlayCircleOutlined />} onClick={() => handleRunNow(record)}>立即执行</Button>
                  <Popconfirm title="确认删除该定时任务？" onConfirm={() => handleDelete(record)}>
                    <Button type="link" size="small" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={open}
        title={editingItem ? '编辑定时注册任务' : '新增定时注册任务'}
        onCancel={() => setOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={900}
      >
        <Form form={form} layout="vertical">
          <Card title="调度配置" style={{ marginBottom: 16 }}>
            <Space style={{ width: '100%' }} align="start">
              <Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]} style={{ flex: 2 }}>
                <Input placeholder="例如：每小时注册 Trae 账号" />
              </Form.Item>
              <Form.Item name="enabled" label="启用" valuePropName="checked" style={{ flex: 1 }}>
                <Switch checkedChildren="启用" unCheckedChildren="停用" />
              </Form.Item>
            </Space>
            <Space style={{ width: '100%' }} align="start">
              <Form.Item name="interval_value" label="间隔值" rules={[{ required: true, message: '请输入间隔值' }]} style={{ flex: 1 }}>
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="interval_unit" label="间隔单位" rules={[{ required: true }]} style={{ flex: 1 }}>
                <Select options={INTERVAL_UNIT_OPTIONS} />
              </Form.Item>
            </Space>
          </Card>

          <Card title="注册配置" style={{ marginBottom: 16 }}>
            <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
              <Select options={platformOptions} />
            </Form.Item>
            <Form.Item name="executor_type" label="执行器" rules={[{ required: true }]}>
              <Select options={executorOptions} />
            </Form.Item>
            <Form.Item name="captcha_solver" label="验证码" rules={[{ required: true }]}>
              <Select options={SELECT_FIELDS.default_captcha_solver} />
            </Form.Item>
            <Space style={{ width: '100%' }}>
              <Form.Item name="count_mode" label="数量模式" style={{ flex: 1 }}>
                <Select options={COUNT_MODE_OPTIONS} />
              </Form.Item>
              <Form.Item
                name="count"
                label={countMode === 'dynamic' ? '目标账号数' : '批量数量'}
                extra={countMode === 'dynamic' ? '占位逻辑：按当前有效账号数补足缺口。' : undefined}
                style={{ flex: 1 }}
              >
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="concurrency" label="并发数" style={{ flex: 1 }}>
                <InputNumber min={1} max={5} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="register_delay_seconds" label="每个注册延迟(秒)" style={{ flex: 1 }}>
                <InputNumber min={0} precision={1} step={0.5} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
            <Form.Item name="proxy" label="代理 (可选)">
              <Input placeholder="http://user:pass@host:port" />
            </Form.Item>
          </Card>

          <Card title="邮箱配置" style={{ marginBottom: 16 }}>
            <Form.Item name="mail_provider" label="邮箱服务" rules={[{ required: true }]}>
              <Select options={SELECT_FIELDS.mail_provider} />
            </Form.Item>
            {mailProvider === 'laoudo' && (
              <>
                <Form.Item name="laoudo_email" label="邮箱地址"><Input placeholder="xxx@laoudo.com" /></Form.Item>
                <Form.Item name="laoudo_account_id" label="Account ID"><Input placeholder="563" /></Form.Item>
                <Form.Item name="laoudo_auth" label="JWT Token"><Input.Password placeholder="eyJ..." /></Form.Item>
              </>
            )}
            {mailProvider === 'mail215' && (
              <>
                <Form.Item name="mail215_api_url" label="API URL"><Input placeholder="https://maliapi.215.im/v1" /></Form.Item>
                <Form.Item name="mail215_api_key" label="API Key"><Input.Password placeholder="AC-..." /></Form.Item>
                <Form.Item name="mail215_domain" label="域名 (可选)"><Input placeholder="public.example.com" /></Form.Item>
                <Form.Item name="mail215_address_prefix" label="自定义前缀 (可选)"><Input placeholder="my-prefix" /></Form.Item>
              </>
            )}
            {mailProvider === 'cfworker' && (
              <>
                <Form.Item name="cfworker_api_url" label="API URL"><Input placeholder="https://apimail.example.com" /></Form.Item>
                <Form.Item name="cfworker_admin_token" label="Admin Token"><Input.Password placeholder="abc123" /></Form.Item>
                <Form.Item name="cfworker_domain" label="域名"><Input placeholder="example.com" /></Form.Item>
                <Form.Item name="cfworker_fingerprint" label="Fingerprint (可选)"><Input placeholder="cfb82279f..." /></Form.Item>
              </>
            )}
          </Card>

          {captchaSolver === 'yescaptcha' && (
            <Card title="验证码配置" style={{ marginBottom: 16 }}>
              <Form.Item name="yescaptcha_key" label="YesCaptcha Key"><Input.Password /></Form.Item>
            </Card>
          )}

          {captchaSolver === 'local_solver' && (
            <Card title="本地 Solver 配置" style={{ marginBottom: 16 }}>
              <Form.Item name="solver_url" label="Solver URL"><Input /></Form.Item>
            </Card>
          )}
        </Form>
      </Modal>
    </div>
  )
}

function SolverStatus() {
  const [running, setRunning] = useState<boolean | null>(null)

  const checkSolver = async () => {
    try {
      const d = await apiFetch('/solver/status')
      setRunning(d.running)
    } catch {
      setRunning(false)
    }
  }

  const restartSolver = async () => {
    await apiFetch('/solver/restart', { method: 'POST' })
    setRunning(null)
    setTimeout(checkSolver, 2000)
  }

  useEffect(() => {
    checkSolver()
    const timer = window.setInterval(checkSolver, 5000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <Card title="Turnstile Solver" size="small" style={{ marginBottom: 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <Space size={8}>
          {running === null ? (
            <SyncOutlined spin style={{ color: '#7a8ba3' }} />
          ) : running ? (
            <CheckCircleOutlined style={{ color: '#10b981' }} />
          ) : (
            <CloseCircleOutlined style={{ color: '#ef4444' }} />
          )}
          <span style={{ color: running ? '#10b981' : '#7a8ba3', fontWeight: 500 }}>
            {running === null ? '检测中' : running ? '运行中' : '未运行'}
          </span>
        </Space>
        <Button size="small" onClick={restartSolver}>
          重启 Solver
        </Button>
      </div>
    </Card>
  )
}

function IntegrationsPanel() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [resultModal, setResultModal] = useState({
    open: false,
    title: '',
    ok: true,
    content: '',
  })

  const showResultModal = (title: string, data: unknown, ok = true) => {
    setResultModal({
      open: true,
      title,
      ok,
      content: formatResultText(data),
    })
  }

  const load = async () => {
    setLoading(true)
    try {
      const d = await apiFetch('/integrations/services')
      setItems(d.items || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const doAction = async (key: string, request: Promise<any>) => {
    setBusy(key)
    try {
      const result = await request
      await load()
      message.success('操作完成')
      showResultModal('操作结果', result, true)
    } catch (e: any) {
      message.error(e?.message || '操作失败')
      showResultModal('操作结果', e?.message || e || '操作失败', false)
      await load()
    } finally {
      setBusy('')
    }
  }

  const backfill = async (platforms: string[], label: string, busyKey: string) => {
    setBusy(busyKey)
    try {
      const d = await apiFetch('/integrations/backfill', {
        method: 'POST',
        body: JSON.stringify({ platforms }),
      })
      message.success(`${label} 回填完成：成功 ${d.success} / ${d.total}`)
      showResultModal(`${label} 回填结果`, d, true)
    } catch (e: any) {
      message.error(e?.message || `${label} 回填失败`)
      showResultModal(`${label} 回填结果`, e?.message || e || `${label} 回填失败`, false)
    } finally {
      setBusy('')
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Modal
        open={resultModal.open}
        title={resultModal.title}
        onCancel={() => setResultModal((v) => ({ ...v, open: false }))}
        onOk={() => setResultModal((v) => ({ ...v, open: false }))}
        width={760}
      >
        <Typography.Paragraph style={{ marginBottom: 8, color: resultModal.ok ? '#10b981' : '#ef4444' }}>
          {resultModal.ok ? '操作已完成。' : '操作失败。'}
        </Typography.Paragraph>
        <pre
          style={{
            margin: 0,
            maxHeight: 420,
            overflow: 'auto',
            padding: 12,
            borderRadius: 8,
            background: 'rgba(127,127,127,0.08)',
            fontSize: 12,
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {resultModal.content}
        </pre>
      </Modal>

      <Card title="批量操作">
        <Space wrap>
          <Button loading={busy === 'start-all'} onClick={() => doAction('start-all', apiFetch('/integrations/services/start-all', { method: 'POST' }))}>
            启动全部（已安装）
          </Button>
          <Button loading={busy === 'stop-all'} onClick={() => doAction('stop-all', apiFetch('/integrations/services/stop-all', { method: 'POST' }))}>
            停止全部
          </Button>
          <Button loading={loading} onClick={load}>
            刷新状态
          </Button>
        </Space>
      </Card>

      {items.map((item) => (
        <Card key={item.name} title={item.label}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <div>
              状态：
              <Tag color={item.running ? 'green' : 'default'} style={{ marginLeft: 8 }}>
                {item.running ? '运行中' : '未运行'}
              </Tag>
              <Tag color={item.repo_exists ? 'blue' : 'orange'} style={{ marginLeft: 8 }}>
                {item.repo_exists ? '已安装' : '未安装'}
              </Tag>
              {item.pid ? <span style={{ marginLeft: 8 }}>PID: {item.pid}</span> : null}
            </div>
            <div>插件目录：<Typography.Text copyable>{item.repo_path}</Typography.Text></div>
            {item.url ? <div>地址：<Typography.Text copyable>{item.url}</Typography.Text></div> : null}
            {item.management_url ? <div>管理页：<Typography.Text copyable>{item.management_url}</Typography.Text></div> : null}
            {item.management_key ? <div>登录口令：<Typography.Text copyable>{item.management_key}</Typography.Text></div> : null}
            <div>日志：<Typography.Text copyable>{item.log_path}</Typography.Text></div>
            {item.last_error ? <div style={{ color: '#ef4444' }}>最近错误：{item.last_error}</div> : null}
            <Space wrap>
              {item.management_url ? (
                <Button onClick={() => window.open(item.management_url, '_blank')}>
                  打开管理页
                </Button>
              ) : null}
              {!item.repo_exists ? (
                <Button
                  type="primary"
                  loading={busy === `install-${item.name}`}
                  onClick={() => doAction(`install-${item.name}`, apiFetch(`/integrations/services/${item.name}/install`, { method: 'POST' }))}
                >
                  安装
                </Button>
              ) : null}
              <Button
                loading={busy === `start-${item.name}`}
                disabled={!item.repo_exists}
                onClick={() => doAction(`start-${item.name}`, apiFetch(`/integrations/services/${item.name}/start`, { method: 'POST' }))}
              >
                启动
              </Button>
              <Button
                loading={busy === `stop-${item.name}`}
                onClick={() => doAction(`stop-${item.name}`, apiFetch(`/integrations/services/${item.name}/stop`, { method: 'POST' }))}
              >
                停止
              </Button>
              {item.name === 'grok2api' ? (
                <Button
                  loading={busy === 'backfill-grok'}
                  onClick={() => backfill(['grok'], 'Grok', 'backfill-grok')}
                >
                  回填现有 Grok 账号
                </Button>
              ) : null}
              {item.name === 'kiro-manager' ? (
                <Button
                  loading={busy === 'backfill-kiro'}
                  onClick={() => backfill(['kiro'], 'Kiro', 'backfill-kiro')}
                >
                  回填现有 Kiro 账号
                </Button>
              ) : null}
            </Space>
          </Space>
        </Card>
      ))}
    </div>
  )
}

export default function Settings() {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [activeTab, setActiveTab] = useState('register')
  const [configValues, setConfigValues] = useState<Record<string, any>>({})

  useEffect(() => {
    apiFetch('/config').then((data) => {
      form.setFieldsValue(data)
      setConfigValues(data || {})
    })
  }, [form])

  const save = async () => {
    setSaving(true)
    try {
      const values = form.getFieldsValue()
      await apiFetch('/config', { method: 'PUT', body: JSON.stringify({ data: values }) })
      message.success('保存成功')
      setConfigValues(values)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const currentTab = TAB_ITEMS.find((t) => t.key === activeTab) as TabConfig

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>全局配置</h1>
        <p style={{ color: '#7a8ba3', marginTop: 4 }}>配置将持久化保存，注册任务自动使用</p>
      </div>

      <div style={{ display: 'flex', gap: 24 }}>
        <div style={{ width: 200 }}>
          <Tabs
            tabPosition="left"
            activeKey={activeTab}
            onChange={setActiveTab}
            items={TAB_ITEMS.map((t) => ({
              key: t.key,
              label: (
                <span>
                  {t.icon}
                  <span style={{ marginLeft: 8 }}>{t.label}</span>
                </span>
              ),
            }))}
          />
        </div>

        <div style={{ flex: 1 }}>
          {activeTab === 'integrations' ? (
            <IntegrationsPanel />
          ) : activeTab === 'scheduled-register' ? (
            <ScheduledRegisterPanel configValues={configValues} />
          ) : (
            <Form form={form} layout="vertical">
              {activeTab === 'captcha' ? <SolverStatus /> : null}
              {currentTab.sections.map((section) => (
                <ConfigSection key={section.title} section={section} />
              ))}
              <Button type="primary" icon={<SaveOutlined />} onClick={save} loading={saving} block>
                {saved ? '已保存 ✓' : '保存配置'}
              </Button>
            </Form>
          )}
        </div>
      </div>
    </div>
  )
}
