// Server-authoritative fireball projectile.
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "AbilityProjectile.generated.h"

class USphereComponent;
class UStaticMeshComponent;
class UProjectileMovementComponent;
class UNiagaraComponent;
class UNiagaraSystem;
class UPointLightComponent;
class UMaterialInterface;
class USoundBase;

UCLASS()
class AAbilityProjectile : public AActor
{
	GENERATED_BODY()

public:
	AAbilityProjectile();

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Projectile")
	TObjectPtr<USphereComponent> CollisionComponent;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Projectile")
	TObjectPtr<UStaticMeshComponent> MeshComponent;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Projectile")
	TObjectPtr<UProjectileMovementComponent> ProjectileMovement;

	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Projectile")
	float Damage = 20.f;

	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Projectile")
	float LifeSeconds = 4.f;

	/** World-space trail that streams behind the flight. Set from the system asset on BeginPlay. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Projectile|Cosmetics")
	TObjectPtr<UNiagaraComponent> TrailComponent;

	/** So the fireball actually lights the arena as it travels. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Projectile|Cosmetics")
	TObjectPtr<UPointLightComponent> GlowLight;

	UPROPERTY(EditDefaultsOnly, Category = "Projectile|Cosmetics")
	TObjectPtr<UNiagaraSystem> TrailEffect;

	UPROPERTY(EditDefaultsOnly, Category = "Projectile|Cosmetics")
	TObjectPtr<UNiagaraSystem> ImpactEffect;

	UPROPERTY(EditDefaultsOnly, Category = "Projectile|Cosmetics")
	TObjectPtr<USoundBase> ImpactSound;

	UPROPERTY(EditDefaultsOnly, Category = "Projectile|Cosmetics")
	TObjectPtr<UMaterialInterface> BodyMaterial;

	/** Server only: sets owner/instigator, speed and damage, then starts the flight. */
	void LaunchProjectile(AController* InInstigatorController, float InSpeed, float InDamage);

protected:
	virtual void BeginPlay() override;
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

	UFUNCTION()
	void HandleOverlap(UPrimitiveComponent* OverlappedComponent, AActor* OtherActor, UPrimitiveComponent* OtherComp, int32 OtherBodyIndex, bool bFromSweep, const FHitResult& SweepResult);

	UFUNCTION(NetMulticast, Unreliable)
	void MulticastExplode();

	void Explode(AActor* DirectHitActor);

	UPROPERTY(Replicated)
	TObjectPtr<AController> InstigatorController;
};
