"use client";

import { AdminPageHeader, JsonBlock } from "@/components/admin-common";
import { useAdmin } from "@/components/admin-shell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function AdminSettingsPage() {
  const admin = useAdmin();

  return (
    <>
      <AdminPageHeader
        title="Admin settings"
        description="Your admin roles and effective permissions from the backend."
      />

      <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Identity</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-normal text-muted-foreground">Email</p>
              <p className="mt-1 text-sm font-medium">{admin.email}</p>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-normal text-muted-foreground">User ID</p>
              <p className="mt-1 break-all text-sm font-medium">{admin.id}</p>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-normal text-muted-foreground">Roles</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {admin.roles.map((role) => (
                  <Badge key={role.id} variant="outline">
                    {role.name}
                  </Badge>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Effective permissions</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-4 flex flex-wrap gap-2">
              {admin.permissions.map((permission) => (
                <Badge key={permission} variant="secondary">
                  {permission}
                </Badge>
              ))}
            </div>
            <JsonBlock value={admin} />
          </CardContent>
        </Card>
      </div>
    </>
  );
}
