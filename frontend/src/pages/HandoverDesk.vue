<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const shifts = ref([])
const zones = ref([])
const fromId = ref(1)
const toId = ref(2)
const selectedZones = ref([])
const current = ref(null)
const msg = ref('')
const ok = ref(false)

const fromShift = computed(() => shifts.value.find((s) => s.id === Number(fromId.value)))
// 可选责任区 = 交班方当前持权的责任区
const fromDutyZones = computed(() =>
  fromShift.value ? fromShift.value.zones.filter((z) => z.on_duty) : []
)

async function refresh() {
  shifts.value = await getJson('/api/shifts')
  zones.value = await getJson('/api/zones')
  const logs = await getJson('/api/handovers')
  current.value = logs.find((h) => h.status === '进行中') || null
  if (current.value) {
    current.value = await getJson(`/api/handovers/${current.value.id}`)
  }
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
})

function toggleZone(zid) {
  const set = new Set(selectedZones.value)
  if (set.has(zid)) set.delete(zid)
  else set.add(zid)
  selectedZones.value = [...set]
}

async function start() {
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson('/api/handovers/start', {
      from_shift_id: Number(fromId.value),
      to_shift_id: Number(toId.value),
      zone_ids: selectedZones.value.length ? [...selectedZones.value] : null,
    })
    ok.value = true
    msg.value = `已开交班单 #${current.value.id}，覆盖责任区 ${
      current.value.zones.map((z) => z.name).join('、')
    }，清单 ${current.value.batch_count} 批`
    selectedZones.value = []
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function complete() {
  if (!current.value) return
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson(`/api/handovers/${current.value.id}/complete`)
    ok.value = current.value.status === '已完成'
    msg.value = current.value.status === '已完成'
      ? `交班单 #${current.value.id} 已完成：值班权同事务切换，批次已过户`
      : `交班单 #${current.value.id} 失败：${current.value.reason || '原子切换未生效'}`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>交班办理</h1>
    <p class="lead">
      清单跨多个责任区时，每个责任区都必须满足双方资格与单值班约束，否则整单拒绝；
      完成交班在同一事务内切换值班权（卸任+就任原子生效），不留空窗、不假成功。
    </p>

    <div class="panel">
      <div class="row">
        <label>交班方
          <select v-model.number="fromId" @change="selectedZones = []">
            <option v-for="s in shifts" :key="s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <label>接班方
          <select v-model.number="toId">
            <option v-for="s in shifts" :key="'t'+s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <button class="btn" :disabled="!!current" @click="start">发起交班</button>
        <button class="btn ghost" :disabled="!current" @click="complete">完成交班</button>
      </div>
      <div class="row" v-if="fromDutyZones.length">
        <label style="flex-direction:row;align-items:center;gap:10px">
          交接责任区（不勾=全部持权责任区）：
          <label
            v-for="z in fromDutyZones"
            :key="z.id"
            style="flex-direction:row;align-items:center;gap:4px;color:var(--ink)"
          >
            <input type="checkbox" :value="z.id" :checked="selectedZones.includes(z.id)" @change="toggleZone(z.id)" />
            {{ z.name }}
          </label>
        </label>
      </div>
      <p class="msg" :class="{ ok }" style="white-space:pre-wrap">{{ msg }}</p>
    </div>

    <div class="panel" v-if="current">
      <h2>当前交班单 #{{ current.id }} · {{ current.status }}</h2>
      <p class="lead" style="margin-bottom:12px">
        {{ current.from_shift_name }} → {{ current.to_shift_name }} ·
        责任区 {{ current.zones.map((z) => z.name).join('、') }} ·
        清单 {{ current.batch_count }} 批
      </p>
      <table>
        <thead><tr><th>业务键</th><th>标题</th><th>责任区</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in current.items || []" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.zone_name || '—' }}</td>
            <td>{{ b.status }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin:12px 0 6px;font-size:14px">值班切换证明（完成后同事务落库）</h3>
      <table v-if="current.transitions && current.transitions.length">
        <thead>
          <tr><th>证明</th><th>责任区</th><th>旧值班</th><th>新值班</th><th>切换时刻</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in current.transitions" :key="t.id">
            <td>#{{ t.id }}</td>
            <td>{{ t.zone_name }}</td>
            <td>{{ t.from_shift_name || '—' }}</td>
            <td><span class="tag on">{{ t.to_shift_name }}</span></td>
            <td style="white-space:nowrap">{{ t.switched_at }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else style="color:var(--muted);font-size:12px">
        尚未完成：无切换证明，值班权仍属交班方，不会空窗也不会假成功。
      </p>
    </div>

    <div class="panel">
      <h2>各责任区当前值班</h2>
      <table>
        <thead><tr><th>责任区</th><th>当前值班班次</th><th>值班人</th><th>持权起始</th></tr></thead>
        <tbody>
          <tr v-for="z in zones" :key="z.id">
            <td>{{ z.name }}</td>
            <td>
              <span v-if="z.duty_shift_id" class="tag on">
                {{ z.duty_shift_name }}（#{{ z.duty_shift_id }}）
              </span>
              <span v-else class="tag">无值班（空窗）</span>
            </td>
            <td>{{ z.duty_keeper || '—' }}</td>
            <td>{{ z.duty_since || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
