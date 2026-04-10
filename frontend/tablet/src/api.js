import axios from 'axios'

const BASE = '/api/v1'

export const api = {
  login: (email, password) => axios.post(`${BASE}/auth/login`, { email, password }).then(r => r.data),
  getBusinesses: () => axios.get(`${BASE}/businesses/`).then(r => r.data),
  getBusiness: (id) => axios.get(`${BASE}/businesses/${id}`).then(r => r.data),
  createBusiness: (payload) => axios.post(`${BASE}/businesses/`, payload).then(r => r.data),
  updateBusiness: (id, payload) => axios.put(`${BASE}/businesses/${id}`, payload).then(r => r.data),
  getDashboard: (id) => axios.get(`${BASE}/dashboard/${id}/summary`).then(r => r.data),
  getConversations: (id) => axios.get(`${BASE}/conversations/${id}`).then(r => r.data),
  simulate: (scenario, message, businessId, conversationId) =>
    axios.post(`${BASE}/events/simulate`, {
      scenario,
      message,
      business_id: businessId,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }).then(r => r.data),
  inbound: (payload) =>
    axios.post(`${BASE}/events/inbound`, payload).then(r => r.data),
}
