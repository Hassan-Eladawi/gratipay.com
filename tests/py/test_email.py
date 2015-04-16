import json

from gratipay.exceptions import CannotRemovePrimaryEmail, EmailAlreadyTaken, EmailNotVerified
from gratipay.exceptions import TooManyEmailAddresses
from gratipay.models.participant import Participant
from gratipay.testing.emails import EmailHarness
from gratipay.utils import emails


class TestEmail(EmailHarness):

    def setUp(self):
        EmailHarness.setUp(self)
        self.alice = self.make_participant('alice', claimed_time='now')

    def hit_email_spt(self, action, address, user='alice', should_fail=False):
        P = self.client.PxST if should_fail else self.client.POST
        data = {'action': action, 'address': address}
        headers = {'HTTP_ACCEPT_LANGUAGE': 'en'}
        return P('/alice/emails/modify.json', data, auth_as=user, **headers)

    def verify_email(self, email, nonce, username='alice', should_fail=False):
        url = '/%s/emails/verify.html?email=%s&nonce=%s' % (username, email, nonce)
        G = self.client.GxT if should_fail else self.client.GET
        return G(url, auth_as=username)

    def verify_and_change_email(self, old_email, new_email, username='alice'):
        self.hit_email_spt('add-email', old_email)
        nonce = Participant.from_username(username).get_email(old_email).nonce
        self.verify_email(old_email, nonce)
        self.hit_email_spt('add-email', new_email)

    def test_participant_can_add_email(self):
        response = self.hit_email_spt('add-email', 'alice@gratipay.com')
        actual = json.loads(response.body)
        assert actual

    def test_adding_email_sends_verification_email(self):
        self.hit_email_spt('add-email', 'alice@gratipay.com')
        assert self.mailer.call_count == 1
        last_email = self.get_last_email()
        assert last_email['to'][0]['email'] == 'alice@gratipay.com'
        expected = "We've received a request to connect alice@gratipay.com to the alice account on Gratipay"
        assert expected in last_email['text']

    def test_verification_email_doesnt_contain_unsubscribe(self):
        self.hit_email_spt('add-email', 'alice@gratipay.com')
        last_email = self.get_last_email()
        assert "To stop receiving" not in last_email['text']

    def test_adding_second_email_sends_verification_notice(self):
        self.verify_and_change_email('alice1@example.com', 'alice2@example.com')
        assert self.mailer.call_count == 3
        last_email = self.get_last_email()
        assert last_email['to'][0]['email'] == 'alice1@example.com'
        expected = "We are connecting alice2@example.com to the alice account on Gratipay"
        assert expected in last_email['text']

    def test_post_anon_returns_403(self):
        response = self.hit_email_spt('add-email', 'anon@gratipay.com', user=None, should_fail=True)
        assert response.code == 403

    def test_post_with_no_at_symbol_is_400(self):
        response = self.hit_email_spt('add-email', 'gratipay.com', should_fail=True)
        assert response.code == 400

    def test_post_with_no_period_symbol_is_400(self):
        response = self.hit_email_spt('add-email', 'test@gratipay', should_fail=True)
        assert response.code == 400

    def test_verify_email_without_adding_email(self):
        response = self.verify_email('', 'sample-nonce')
        assert 'Missing Info' in response.body

    def test_verify_email_wrong_nonce(self):
        self.hit_email_spt('add-email', 'alice@example.com')
        nonce = 'fake-nonce'
        r = self.alice.verify_email('alice@gratipay.com', nonce)
        assert r == emails.VERIFICATION_FAILED
        self.verify_email('alice@example.com', nonce)
        expected = None
        actual = Participant.from_username('alice').email_address
        assert expected == actual

    def test_verify_email_a_second_time_returns_redundant(self):
        address = 'alice@example.com'
        self.hit_email_spt('add-email', address)
        nonce = self.alice.get_email(address).nonce
        r = self.alice.verify_email(address, nonce)
        r = self.alice.verify_email(address, nonce)
        assert r == emails.VERIFICATION_REDUNDANT

    def test_verify_email_expired_nonce(self):
        address = 'alice@example.com'
        self.hit_email_spt('add-email', address)
        self.db.run("""
            UPDATE emails
               SET verification_start = (now() - INTERVAL '25 hours')
             WHERE participant = 'alice'
        """)
        nonce = self.alice.get_email(address).nonce
        r = self.alice.verify_email(address, nonce)
        assert r == emails.VERIFICATION_EXPIRED
        actual = Participant.from_username('alice').email_address
        assert actual == None

    def test_verify_email(self):
        self.hit_email_spt('add-email', 'alice@example.com')
        nonce = self.alice.get_email('alice@example.com').nonce
        self.verify_email('alice@example.com', nonce)
        expected = 'alice@example.com'
        actual = Participant.from_username('alice').email_address
        assert expected == actual

    def test_verified_email_is_not_changed_after_update(self):
        self.verify_and_change_email('alice@example.com', 'alice@example.net')
        expected = 'alice@example.com'
        actual = Participant.from_username('alice').email_address
        assert expected == actual

    def test_get_emails(self):
        self.verify_and_change_email('alice@example.com', 'alice@example.net')
        emails = self.alice.get_emails()
        assert len(emails) == 2

    def test_verify_email_after_update(self):
        self.verify_and_change_email('alice@example.com', 'alice@example.net')
        nonce = self.alice.get_email('alice@example.net').nonce
        self.verify_email('alice@example.net', nonce)
        expected = 'alice@example.com'
        actual = Participant.from_username('alice').email_address
        assert expected == actual

    def test_nonce_is_reused_when_resending_email(self):
        self.hit_email_spt('add-email', 'alice@example.com')
        nonce1 = self.alice.get_email('alice@example.com').nonce
        self.hit_email_spt('resend', 'alice@example.com')
        nonce2 = self.alice.get_email('alice@example.com').nonce
        assert nonce1 == nonce2

    def test_cannot_update_email_to_already_verified(self):
        bob = self.make_participant('bob', claimed_time='now')
        self.alice.add_email('alice@gratipay.com')
        nonce = self.alice.get_email('alice@gratipay.com').nonce
        r = self.alice.verify_email('alice@gratipay.com', nonce)
        assert r == emails.VERIFICATION_SUCCEEDED

        with self.assertRaises(EmailAlreadyTaken):
            bob.add_email('alice@gratipay.com')
            nonce = bob.get_email('alice@gratipay.com').nonce
            bob.verify_email('alice@gratipay.com', nonce)

        email_alice = Participant.from_username('alice').email_address
        assert email_alice == 'alice@gratipay.com'

    def test_cannot_add_too_many_emails(self):
        self.alice.add_email('alice@gratipay.com')
        self.alice.add_email('alice@gratipay.net')
        self.alice.add_email('alice@gratipay.org')
        self.alice.add_email('alice@gratipay.co.uk')
        self.alice.add_email('alice@gratipay.io')
        self.alice.add_email('alice@gratipay.co')
        self.alice.add_email('alice@gratipay.eu')
        self.alice.add_email('alice@gratipay.asia')
        self.alice.add_email('alice@gratipay.museum')
        self.alice.add_email('alice@gratipay.py')
        with self.assertRaises(TooManyEmailAddresses):
            self.alice.add_email('alice@gratipay.coop')

    def test_account_page_shows_emails(self):
        self.verify_and_change_email('alice@example.com', 'alice@example.net')
        body = self.client.GET("/alice/settings/", auth_as="alice").body
        assert 'alice@example.com' in body
        assert 'alice@example.net' in body

    def test_set_primary(self):
        self.verify_and_change_email('alice@example.com', 'alice@example.net')
        self.verify_and_change_email('alice@example.net', 'alice@example.org')
        self.hit_email_spt('set-primary', 'alice@example.com')

    def test_cannot_set_primary_to_unverified(self):
        with self.assertRaises(EmailNotVerified):
            self.hit_email_spt('set-primary', 'alice@example.com')

    def test_remove_email(self):
        # Can remove unverified
        self.hit_email_spt('add-email', 'alice@example.com')
        self.hit_email_spt('remove', 'alice@example.com')

        # Can remove verified
        self.verify_and_change_email('alice@example.com', 'alice@example.net')
        self.verify_and_change_email('alice@example.net', 'alice@example.org')
        self.hit_email_spt('remove', 'alice@example.net')

        # Cannot remove primary
        with self.assertRaises(CannotRemovePrimaryEmail):
            self.hit_email_spt('remove', 'alice@example.com')

    def test_html_escaping(self):
        self.alice.add_email("foo'bar@example.com")
        last_email = self.get_last_email()
        assert 'foo&#39;bar' in last_email['html']
        assert '&#39;' not in last_email['text']

    def test_can_dequeue_an_email(self):
        larry = self.make_participant('larry', email_address='larry@example.com')
        larry.queue_email("verification")

        Participant.dequeue_emails()
        assert self.mailer.call_count == 1
        last_email = self.get_last_email()
        assert last_email['to'][0]['email'] == 'larry@example.com'
        expected = "connect larry"
        assert expected in last_email['text']
