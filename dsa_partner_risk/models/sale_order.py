# -*- coding: utf-8 -*-
# © 2009 Albert Cervera i Areny <http://www.nan-tic.com)>
# © 2018 Comunitea - Javier Colmenero <javier@comunitea.com>
# © 2011 Pexego Sistemas Informáticos.
#        Alberto Luengo Cabanillas <alberto@pexego.es>
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html
import logging
from odoo import fields, models, api, _

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    
    @api.multi
    def check_limit(self):
        self.ensure_one()
        partner = self.partner_id
        user_id = self.env['res.users'].search([
            ('partner_id', '=', partner.id)], limit=1)
        if user_id and not user_id.has_group('base.group_portal') or not \
                user_id:
            moveline_obj = self.env['account.move.line']
            movelines = moveline_obj.search(
                [('partner_id', '=', partner.id),
                 ('account_id.user_type_id.name', 'in',
                  ['Receivable', 'Payable'])]
            )
            confirm_sale_order = self.search([('partner_id', '=', partner.id),
                                              ('state', '=', 'sale')])
            debit, credit = 0.0, 0.0
            amount_total = 0.0
            for status in confirm_sale_order:
                amount_total += status.amount_total
            for line in movelines:
                credit += line.credit
                debit += line.debit
            partner_credit_limit = (partner.credit_limit - debit) + credit
            available_credit_limit = \
                ((partner_credit_limit -
                  (amount_total - debit)) + self.amount_total)

            if (amount_total - debit) > partner_credit_limit:
                if not partner.over_credit:
                    msg = 'Your available credit limit' \
                          ' Amount = %s \nCheck "%s" Accounts or Credit ' \
                          'Limits.' % (available_credit_limit,
                                       self.partner_id.name)
                    raise UserError(_('You can not confirm Sale '
                                      'Order. \n' + msg))
                partner.write(
                    {'credit_limit': credit - debit + self.amount_total})
            return True    

    amount_invoiced = fields.Float(compute='_amount_invoiced',
                                   string='Invoiced Amount')
    state = fields.Selection(
        selection_add=[('wait_risk', 'Waiting Risk Approval')])
    
    @api.multi
    def action_draft(self):
        orders = self.filtered(lambda s: s.state in ['cancel', 'sent','wait_risk'])
        return orders.write({
            'state': 'draft',
            'signature': False,
            'signed_by': False,
        })    

    @api.multi
    def _amount_invoiced(self):
        for order in self:
            if order.invoiced:
                amount = order.amount_total
            else:
                amount = 0.0
                for line in order.order_line:
                    amount += line.amount_invoiced
            order.amount_invoiced = amount

    @api.onchange('partner_id')
    def onchange_partner_id(self):
        """
        Warning when a cash customer is selected
        """
        super(SaleOrder, self).onchange_partner_id()
        result = {}
        if self.partner_id:
            partner = self.partner_id.commercial_partner_id
            if partner.cash_credit:
                result['warning'] = {
                    'title': _('Credit Limit Exceeded'),
                    'message': _('Warning: Credit Limit Exceeded.\n\nThis \
                        partner has a credit limit of %(limit).2f and already \
                        has a debt of %(debt).2f.') % {
                        'limit': partner.credit_limit,
                        'debt': partner.total_debt,
                    }
                }
        return result

    @api.multi
    def draft_to_risk(self):
        for order in self:
            partner = order.partner_id 
            politic_risk1 = (partner.available_risk - order.amount_total < 0.0)
            sMemo = ''
            if politic_risk1:
                sMemo += '\n .- Se sobrepasa el limite de credito' 
            politic_risk2 = (partner.pending_docum_debi > partner.max_invoice_credit)
            if politic_risk2:
                sMemo += '\n .- La cantidad de documentos pendientes sobrepasa la permitida'                            
            politic_risk3 = (partner.pending_credit_days > partner.max_dias_credit)
            if politic_risk3:
                sMemo = '\n .- Se excedio la en los dias de credito'             
            politic_risk = politic_risk1 or politic_risk2 or politic_risk3 
            if not politic_risk:
                order.action_confirm()
            else:
                context = dict(self._context or {})                
                context['memo'] = sMemo
                return {
                        'type': 'ir.actions.act_window',
                        'res_model': 'open.risk.sale',
                        'view_type': 'form',
                        'view_mode': 'form',
                        'target': 'new',
                        'context': context,
                        }
      
    @api.multi
    def risk_to_cancel(self):
        return self.action_cancel()

    @api.multi
    def risk_to_router(self):
        for order in self:
            return order.with_context(new_state='assigned').action_confirm()
            partner = order.partner_id
            if not partner.credit_limit or \
                    partner.available_risk - order.amount_total >= 0.0:
                order.with_context(new_state='assigned').action_confirm()
            elif partner.credit_limit or \
                    partner.available_risk - order.amount_total < 0.0:
                return self.write({'state': 'wait_risk'})
            
            
    @api.multi
    def action_confirm(self):
        res = super(SaleOrder,self).action_confirm()
        new_state = self.env.context.get('new_state',False)
        if new_state:        
            if self._get_forbidden_state_confirm() & set(self.mapped('state')):
                raise UserError(_(
                    'It is not allowed to confirm an order in the following states: %s'
                ) % (', '.join(self._get_forbidden_state_confirm())))
    
            for order in self.filtered(lambda order: order.partner_id not in order.message_partner_ids):
                order.message_subscribe([order.partner_id.id])
            if new_state=='wait_risk':
                self.write({
                    'state': new_state
                })                
            else:
                self.write({
                    'state': 'sale',
                    'confirmation_date': fields.Datetime.now()
                })
            self._action_confirm()
            if self.env['ir.config_parameter'].sudo().get_param('sale.auto_done_setting'):
                self.action_done()
            return True    
        return res    
    
    @api.multi
    def _action_confirm(self):
        super(SaleOrder, self)._action_confirm()
        new_state = self.env.context.get('new_state',False)
        if new_state and new_state!='wait_risk':     
            for order in self:
                order.mapped('picking_ids').action_wait_risk()                             

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    amount_invoiced = fields.Float(compute='_amount_invoiced',
                                   string='Invoiced Amount')

    @api.multi
    def _amount_invoiced(self):

        for line in self:
            # Calculate invoiced amount with taxes included.
            # Note that if a line is only partially invoiced we consider
            # the invoiced amount 0.
            # The problem is we can't easily know if the user changed amounts
            # once the invoice was created
            if line.invoiced:
                line.amount_invoiced = line.price_subtotal + \
                    line._tax_amount()
            else:
                line.amount_invoiced = 0.0

    @api.multi
    def _tax_amount(self, cr, uid, line):
        val = 0.0
        self.ensure_one()
        v = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
        for c in line.tax_id.compute_all(
                v, quantity=line.product_uos_qty,
                product=line.product_id,
                partner=line.order_id.partner_id)['taxes']:
            val += c['amount']
        return val
